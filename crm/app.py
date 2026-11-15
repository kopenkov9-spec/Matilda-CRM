import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ValidationError
from .store import Store, Conflict, uid, now, encode
from .schema import KINDS, STATUSES, Record, Plan
from .ai import LocalAI, ROOT
from .completion import NeedsCompletion, CompletionInput, make_draft, save_entries
from .lifecycle import DesktopLifecycle
from .network import phone_status

MAX_UPLOAD = 12 * 1024 * 1024

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

class MessageInput(BaseModel):
    id: UUID
    text: str = Field(min_length=1, max_length=6000)
    recorded_at: datetime
    timezone: str = 'Europe/Sofia'
    mode: str = 'write'

class Worker:
    def __init__(self, store, ai):
        self.store, self.ai = store, ai
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run,daemon=True)

    def run(self):
        while not self.stop.is_set():
            if not self.once():
                self.stop.wait(0.5)

    def once(self):
        with self.store.connect(write=True) as c:
            row = c.execute("SELECT * FROM messages WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False
            m = dict(row)
            audio = bool(m['audio_path'] and not m['text'])
            phase = 'transcribing' if audio else 'processing'
            c.execute('UPDATE messages SET status=?,updated_at=? WHERE id=?',(phase,now(),m['id']))
        try:
            if audio:
                text = self.ai.transcribe(m['audio_path'])
                with self.store.connect(write=True) as c:
                    c.execute("UPDATE messages SET text=?,status='needs_transcript',updated_at=? WHERE id=? AND revision=? AND status=?",
                              (text,now(),m['id'],m['revision'],phase))
            else:
                with self.store.connect() as c:
                    context = self.store.records(c)
                plan = self.ai.extract(m,context)
                with self.store.connect(write=True) as c:
                    current = self.store.message(c,m['id'])
                    if current['status'] != phase or current['revision'] != m['revision']:
                        return True
                    result = None
                    if plan.intent == 'query':
                        result = self.store.query(c,plan.query,m['timezone'])
                        status = 'done'
                    else:
                        status = 'needs_review' if plan.intent == 'write' else 'needs_completion'
                    c.execute('UPDATE messages SET status=?,proposal=?,result=?,error=NULL,updated_at=? WHERE id=?',
                              (status,plan.model_dump_json(),encode(result) if result else None,now(),m['id']))
        except NeedsCompletion as error:
            with self.store.connect(write=True) as c:
                c.execute("UPDATE messages SET status='needs_completion',proposal=?,error=NULL,updated_at=? WHERE id=? AND revision=? AND status=?",
                          (error.plan.model_dump_json(),now(),m['id'],m['revision'],phase))
        except Exception as error:
            if isinstance(error,(httpx.HTTPError,ConnectionError)):
                reason = 'Phi-4-mini не е достъпен. Стартирайте модела и опитайте отново.'
            elif isinstance(error,ValidationError):
                reason = 'Моделът върна невалидни данни. Уточнете текста и опитайте отново.'
            elif isinstance(error,ValueError):
                reason = str(error)[:500]
            else:
                reason = 'Обработката не завърши. Можете да редактирате текста и да опитате отново.'
            with self.store.connect(write=True) as c:
                c.execute("UPDATE messages SET status='error',error=?,updated_at=? WHERE id=? AND revision=? AND status=?",
                          (reason,now(),m['id'],m['revision'],phase))
        finally:
            if audio:
                # Original audio is removed immediately after processing; the transcript remains.
                Path(m['audio_path']).unlink(missing_ok=True)
                with self.store.connect(write=True) as c:
                    c.execute('UPDATE messages SET audio_path=NULL WHERE id=?',(m['id'],))
        return True

def create_app(data_dir=None, ai=None, start_worker=True):
    store = Store(data_dir or os.environ.get('CRM_DATA_DIR',ROOT/'data'))
    ai = ai or LocalAI()
    key_path = store.root/'owner-key.txt'
    if not key_path.exists():
        key_path.write_text(secrets.token_urlsafe(32),encoding='utf-8')
    owner_key = key_path.read_text(encoding='utf-8').strip()
    workspace_path = store.root/'workspace-id.txt'
    if not workspace_path.exists():
        workspace_path.write_text(uid(),encoding='utf-8')
    workspace_id = workspace_path.read_text(encoding='utf-8').strip()
    worker = Worker(store,ai)
    attempts = defaultdict(deque)
    rate_lock = threading.Lock()
    desktop_lifecycle = DesktopLifecycle()

    @asynccontextmanager
    async def lifespan(app):
        with store.connect(write=True) as c:
            c.execute("UPDATE messages SET status='error',error='Обработката беше прекъсната при рестарт. Опитайте отново.' WHERE status IN ('processing','transcribing')")
            # Preserve queued recordings; discard recordings from interrupted or cancelled work.
            stale = c.execute("SELECT audio_path FROM messages WHERE audio_path IS NOT NULL AND status!='queued'").fetchall()
            c.execute("UPDATE messages SET audio_path=NULL WHERE status!='queued'")
        for row in stale:
            Path(row['audio_path']).unlink(missing_ok=True)
        if start_worker:
            worker.thread.start()
        monitor = asyncio.create_task(desktop_lifecycle.watch())
        yield
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        worker.stop.set()
        if start_worker:
            await asyncio.to_thread(worker.thread.join, 2)

    app = FastAPI(title='Matilda CRM',version='0.2.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.state.store, app.state.worker, app.state.ai = store, worker, ai

    @app.exception_handler(ValueError)
    async def value_error(request,error):
        return JSONResponse({'detail':str(error)},status_code=409 if isinstance(error,Conflict) else 422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request,error):
        return JSONResponse({'detail':'Конфликт при запис. Обновете данните и опитайте отново.'},status_code=409)

    @app.middleware('http')
    async def guard(request,call_next):
        if request.url.path.startswith('/api/'):
            try:
                size = int(request.headers.get('content-length','0'))
            except ValueError:
                return JSONResponse({'detail':'Невалидна дължина.'},status_code=400)
            limit = MAX_UPLOAD+65536 if request.url.path=='/api/messages/audio' else 65536
            if size < 0 or size > limit or request.headers.get('transfer-encoding'):
                return JSONResponse({'detail':'Заявката е твърде голяма.'},status_code=413)
            if request.method in ('POST','PUT','PATCH','DELETE'):
                if not request.headers.get('content-length'):
                    return JSONResponse({'detail':'Нужна е дължина на заявката.'},status_code=411)
                if request.headers.get('x-crm-client') != 'mestno':
                    return JSONResponse({'detail':'Невалидна заявка.'},status_code=403)
                origin = request.headers.get('origin')
                if origin and origin != str(request.base_url).rstrip('/'):
                    return JSONResponse({'detail':'Непознат източник на заявката.'},status_code=403)
            if request.url.path not in ('/api/auth/local','/api/auth/pair','/api/health'):
                token = request.cookies.get('crm_session','')
                with store.connect() as c:
                    device = c.execute('SELECT * FROM devices WHERE token_hash=? AND revoked=0 AND expires_at>?',
                                       (digest(token),now())).fetchone()
                if not device:
                    return JSONResponse({'detail':'Свържете това устройство с CRM.'},status_code=401)
                request.state.device = dict(device)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        response.headers['Permissions-Policy'] = 'microphone=(self), camera=(), geolocation=()'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    def rate_limit(request):
        ip = request.client.host
        with rate_lock:
            bucket = attempts[ip]
            t=time.monotonic()
            while bucket and bucket[0]<t-60:
                bucket.popleft()
            if len(bucket)>=8:
                raise HTTPException(429,'Твърде много опити. Изчакайте минута.')
            bucket.append(t)

    def session(c,request,response,name,role):
        token = secrets.token_urlsafe(32)
        ident = uid()
        c.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,0)',
                  (ident,name[:100],digest(token),role,now(),(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()))
        response.set_cookie('crm_session',token,max_age=30*86400,httponly=True,secure=request.url.scheme=='https',samesite='strict')
        return {'id':ident,'name':name,'role':role,'workspace_id':workspace_id}

    def require_admin(request):
        if request.state.device['role']!='admin':
            raise HTTPException(403,'Само основното устройство управлява достъпа.')

    desktop_lifecycle.attach(app, require_admin)

    @app.get('/api/health')
    def health():
        return {'ok':True}

    @app.post('/api/auth/local')
    def local_login(body:dict,request:Request,response:Response):
        rate_limit(request)
        if request.client.host not in ('127.0.0.1','::1','testclient') or not secrets.compare_digest(str(body.get('key','')),owner_key):
            raise HTTPException(403,'Стартирайте CRM от START_CRM.cmd на локалния компютър.')
        with store.connect(write=True) as c:
            return session(c,request,response,'Основен компютър','admin')

    @app.post('/api/auth/pair')
    def pair(body:dict,request:Request,response:Response):
        rate_limit(request)
        if request.url.scheme!='https' and request.client.host not in ('127.0.0.1','::1','testclient'):
            raise HTTPException(403,'За телефон е необходима защитена HTTPS връзка.')
        with store.connect(write=True) as c:
            code = digest(str(body.get('code','')).strip().upper())
            row = c.execute('SELECT * FROM pair_codes WHERE code_hash=? AND used=0 AND expires_at>?',(code,now())).fetchone()
            if not row:
                raise HTTPException(403,'Кодът е невалиден или е изтекъл.')
            c.execute('UPDATE pair_codes SET used=1 WHERE code_hash=?',(code,))
            return session(c,request,response,str(body.get('name','Телефон')),'member')

    @app.get('/api/session')
    def who(request:Request):
        d=request.state.device
        return {**{k:d[k] for k in ('id','name','role')},'workspace_id':workspace_id}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        with store.connect(write=True) as c:
            c.execute('UPDATE devices SET revoked=1 WHERE id=?',(request.state.device['id'],))
        response.delete_cookie('crm_session')
        return {'ok':True}

    @app.post('/api/devices/code')
    def create_code(request:Request):
        require_admin(request)
        code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        expires=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
        with store.connect(write=True) as c:
            c.execute('INSERT INTO pair_codes VALUES (?,?,0)',(digest(code),expires))
        return {'code':code,'expires_at':expires}

    @app.get('/api/devices')
    def devices(request:Request):
        require_admin(request)
        with store.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,name,role,created_at,expires_at,revoked FROM devices ORDER BY created_at DESC')]

    @app.delete('/api/devices/{device_id}')
    def revoke(device_id:str,request:Request):
        require_admin(request)
        with store.connect(write=True) as c:
            c.execute('UPDATE devices SET revoked=1 WHERE id=?',(device_id,))
        return {'ok':True}

    @app.get('/api/system')
    def system():
        return {'llm':ai.available(), 'model':'Phi-4-mini-instruct',
                'speech':(ROOT/'models/whisper-small/model.bin').exists(), 'timezone':'Europe/Sofia',
                'statuses':STATUSES,'version':'0.2.0',
                **phone_status(store.root/'tls')}

    @app.get('/api/phone-certificate')
    def phone_certificate(request:Request):
        require_admin(request)
        path=store.root/'tls/phone-ca.cer'
        if not path.exists():
            raise HTTPException(404,'Първо стартирайте SETUP_PHONE.cmd.')
        return FileResponse(path,filename='mestno-phone-ca.cer',media_type='application/pkix-cert')

    @app.get('/api/records')
    def records(entity:str|None=None,search:str=''):
        with store.connect() as c:
            rows=store.records(c,entity)
        if search:
            rows=[r for r in rows if search.casefold() in (r['title']+' '+r['body']+' '+(r['email'] or '')+' '+(r['phone'] or '')).casefold()]
        return rows

    @app.post('/api/records/{entity}')
    def create(entity:str,body:Record,request:Request):
        with store.connect(write=True) as c:
            return store.save(c,entity,body.model_dump(),request.state.device['id'])

    @app.put('/api/records/{entity}/{record_id}')
    def edit(entity:str,record_id:str,body:dict,request:Request):
        if request.headers.get('x-crm-client')!='mestno':
            raise HTTPException(403,'Невалидна заявка.')
        with store.connect(write=True) as c:
            return store.save(c,entity,body.get('data',{}),request.state.device['id'],record_id,body.get('version'))

    @app.get('/api/audit')
    def audit():
        with store.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY created_at DESC LIMIT 100')]

    def validate_message(body):
        if body.mode not in ('write','query'):
            raise ValueError('Невалиден режим.')
        if body.recorded_at.tzinfo is None:
            raise ValueError('Липсва часова зона на съобщението.')
        try:
            ZoneInfo(body.timezone)
        except Exception:
            raise ValueError('Невалидна часова зона.')
        if body.recorded_at > datetime.now(timezone.utc)+timedelta(minutes=10):
            raise ValueError('Часовникът на устройството е напред. Коригирайте го.')

    def insert_message(c,body,device_id,audio_path=None):
        validate_message(body)
        existing=c.execute('SELECT * FROM messages WHERE id=?',(str(body.id),)).fetchone()
        if existing:
            # All paired devices currently belong to the same owner/workspace.
            # Session renewal must not prevent an idempotent retry from that workspace.
            if not audio_path and (existing['text'] != body.text or existing['mode']!=body.mode):
                raise Conflict('Идентификаторът е използван за друго съдържание.')
            return store.message(c,str(body.id))
        if c.execute("SELECT count(*) FROM messages WHERE status IN ('queued','processing','transcribing')").fetchone()[0]>=30:
            raise HTTPException(429,'Опашката е пълна. Опитайте по-късно.')
        c.execute('''INSERT INTO messages(id,device_id,text,recorded_at,timezone,mode,status,audio_path,created_at,updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (str(body.id),device_id,'' if audio_path else body.text,body.recorded_at.isoformat(),body.timezone,
                   body.mode,'queued',str(audio_path) if audio_path else None,now(),now()))
        return store.message(c,str(body.id))

    @app.post('/api/messages',status_code=202)
    def message_new(body:MessageInput,request:Request):
        with store.connect(write=True) as c:
            return insert_message(c,body,request.state.device['id'])

    @app.post('/api/messages/audio',status_code=202)
    async def audio_new(request:Request,id:str=Form(...),recorded_at:str=Form(...),
                        timezone_name:str=Form('Europe/Sofia'),mode:str=Form('write'),file:UploadFile=File(...)):
        body=MessageInput(id=id,text='audio',recorded_at=recorded_at,timezone=timezone_name,mode=mode)
        validate_message(body)
        if file.content_type not in ('audio/webm','audio/mp4','audio/x-m4a','audio/aac','audio/mpeg','audio/wav','audio/x-wav','audio/ogg','video/mp4','video/webm'):
            raise HTTPException(415,'Неподдържан аудио формат.')
        folder=store.root/'audio'
        folder.mkdir(exist_ok=True)
        path=folder/(uid()+'.audio')
        total=0
        try:
            with path.open('wb') as out:
                while chunk:=await file.read(65536):
                    total+=len(chunk)
                    if total>MAX_UPLOAD:
                        raise HTTPException(413,'Аудиото трябва да е под 12 MB.')
                    out.write(chunk)
            if not total:
                raise ValueError('Празен аудио запис.')
            with store.connect(write=True) as c:
                result=insert_message(c,body,request.state.device['id'],path)
                saved=c.execute('SELECT audio_path FROM messages WHERE id=?',(str(body.id),)).fetchone()[0]
            if saved!=str(path):
                path.unlink(missing_ok=True)
            return result
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get('/api/messages')
    def messages():
        with store.connect() as c:
            ids=c.execute('SELECT id FROM messages ORDER BY created_at DESC LIMIT 60').fetchall()
            return [store.message(c,r['id']) for r in ids]

    @app.get('/api/messages/{message_id}')
    def message_get(message_id:str):
        with store.connect() as c:
            return store.message(c,message_id)

    @app.get('/api/messages/{message_id}/completion')
    def completion_menu(message_id:str):
        with store.connect() as c:
            m=store.message(c,message_id)
            if m['status'] not in ('error','needs_completion','needs_clarification','needs_review','needs_transcript'):
                raise Conflict('За това съобщение няма данни за попълване.')
            if not m['text'].strip():
                raise ValueError('Първо е необходим разпознат текст.')
            return make_draft(m,store.records(c))

    @app.post('/api/messages/{message_id}/complete')
    def complete_message(message_id:str,body:CompletionInput,request:Request):
        with store.connect(write=True) as c:
            m=store.message(c,message_id)
            if m['revision']!=body.revision:
                raise Conflict('Данните са променени. Отворете актуалната форма.')
            if m['status']=='done' and (m.get('result') or {}).get('completion'):
                return m
            if m['status'] not in ('error','needs_completion','needs_clarification','needs_review','needs_transcript'):
                raise Conflict('Съобщението вече се обработва или е приключено.')
            if m['mode']=='query':
                if body.entries or body.query is None:
                    raise ValueError('Запитванията разрешават само справки.')
                if body.query.date_from and body.query.date_to and body.query.date_from>body.query.date_to:
                    raise ValueError('Началната дата трябва да е преди крайната.')
                result=store.query(c,body.query,m['timezone'])
                proposal={'intent':'query','query':body.query.model_dump(),'operations':[],'question':''}
            else:
                if body.query is not None:
                    raise ValueError('Това съобщение е за запис на данни.')
                records=save_entries(store,c,m,body,request.state.device['id'])
                result={'records':records,'count':len(records)}
                proposal={'intent':'write','operations':[],'question':''}
            result['completion']=body.model_dump()
            c.execute("UPDATE messages SET status='done',proposal=?,result=?,error=NULL,updated_at=? WHERE id=?",
                      (encode(proposal),encode(result),now(),message_id))
            return store.message(c,message_id)

    @app.patch('/api/messages/{message_id}')
    def message_edit(message_id:str,body:dict):
        text=str(body.get('text','')).strip()
        if not 0<len(text)<=6000:
            raise ValueError('Въведете текст до 6000 знака.')
        with store.connect(write=True) as c:
            m=store.message(c,message_id)
            if m['status'] in ('done','cancelled') or m['revision']!=body.get('revision'):
                raise Conflict('Съобщението е приключено или има по-нова версия.')
            if m['status']=='transcribing' or c.execute('SELECT audio_path FROM messages WHERE id=?',(message_id,)).fetchone()[0]:
                raise Conflict('Изчакайте транскрипцията да приключи.')
            c.execute("UPDATE messages SET text=?,revision=revision+1,status='queued',proposal=NULL,result=NULL,error=NULL,updated_at=? WHERE id=?",
                      (text,now(),message_id))
            return store.message(c,message_id)

    @app.post('/api/messages/{message_id}/confirm')
    def confirm(message_id:str,body:dict,request:Request):
        with store.connect(write=True) as c:
            m=store.message(c,message_id)
            if m['revision']!=body.get('revision'):
                raise Conflict('Предложението е променено. Прегледайте новата версия.')
            if m['status']=='done' and m['proposal'] and m['proposal']['intent']=='write':
                return m
            if m['status']!='needs_review':
                raise Conflict('Няма готово предложение за потвърждаване.')
            results=store.apply_plan(c,m,m['proposal'],request.state.device['id'])
            c.execute("UPDATE messages SET status='done',result=?,updated_at=? WHERE id=?",
                      (encode({'records':results,'count':len(results)}),now(),message_id))
            return store.message(c,message_id)

    @app.post('/api/messages/{message_id}/cancel')
    def cancel(message_id:str):
        audio_path = None
        with store.connect(write=True) as c:
            m=store.message(c,message_id)
            if m['status']=='done':
                raise Conflict('Записът вече е приложен. Коригирайте съответния CRM запис.')
            if m['status']!='transcribing':
                audio_path = c.execute('SELECT audio_path FROM messages WHERE id=?',(message_id,)).fetchone()[0]
                c.execute('UPDATE messages SET audio_path=NULL WHERE id=?',(message_id,))
            c.execute("UPDATE messages SET status='cancelled',revision=revision+1,updated_at=? WHERE id=?",(now(),message_id))
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)
        return {'ok':True}

    @app.get('/api/export')
    def export(request:Request):
        require_admin(request)
        fd, name = tempfile.mkstemp(prefix='backup-', suffix='.sqlite3', dir=store.root)
        os.close(fd)
        backup = Path(name)
        try:
            with store.connect() as c, closing(sqlite3.connect(backup)) as dest:
                c.backup(dest)
            return FileResponse(backup,filename='matilda-crm-backup.sqlite3',media_type='application/octet-stream',
                                background=BackgroundTask(backup.unlink,missing_ok=True))
        except BaseException:
            backup.unlink(missing_ok=True)
            raise

    app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='static')
    return app
