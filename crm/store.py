import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .schema import KINDS, STATUSES, Record, Plan

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return str(uuid.uuid4())

def encode(value):
    return json.dumps(value, ensure_ascii=False)

class Conflict(ValueError):
    pass

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'crm.sqlite3'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            for table in KINDS:
                c.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                    company_id TEXT REFERENCES companies(id), contact_id TEXT REFERENCES contacts(id),
                    email TEXT, phone TEXT, start_at TEXT, end_at TEXT, due_at TEXT, status TEXT NOT NULL,
                    amount REAL, currency TEXT, version INTEGER NOT NULL DEFAULT 1,
                    source_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, device_id TEXT NOT NULL, text TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, timezone TEXT NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    proposal TEXT, result TEXT, error TEXT, audio_path TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id TEXT PRIMARY KEY, entity TEXT NOT NULL, record_id TEXT NOT NULL,
                    operation TEXT NOT NULL, before_json TEXT, after_json TEXT,
                    message_id TEXT, device_id TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pair_codes (
                    code_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS message_status ON messages(status, created_at);
            ''')

    @contextmanager
    def connect(self, write=False):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        if write:
            c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def records(self, c, entity=None):
        if entity is not None and entity not in KINDS:
            raise ValueError('Непозната таблица.')
        result = []
        for table in ([entity] if entity else KINDS):
            result.extend(dict(r, entity=table) for r in c.execute(f'SELECT * FROM {table} ORDER BY updated_at DESC'))
        return result

    def record(self, c, entity, record_id):
        if entity not in KINDS:
            raise ValueError('Непозната таблица.')
        r = c.execute(f'SELECT * FROM {entity} WHERE id=?', (record_id,)).fetchone()
        if r is None:
            raise ValueError('Записът не е намерен.')
        return dict(r, entity=entity)

    def save(self, c, entity, data, device_id, record_id=None, expected_version=None, message_id=None):
        if entity not in KINDS:
            raise ValueError('Непозната таблица.')
        data = Record.model_validate(data).model_dump()
        data['status'] = data['status'] or STATUSES[entity][0]
        if data['status'] not in STATUSES[entity]:
            raise ValueError('Невалиден статус за този тип запис.')
        for field, table in [('company_id', 'companies'), ('contact_id', 'contacts')]:
            if data[field]:
                self.record(c, table, data[field])
        if entity == 'meetings' and not data['start_at']:
            raise ValueError('Срещата изисква точна начална дата и час.')
        before = None
        if record_id:
            before = self.record(c, entity, record_id)
            if before['version'] != expected_version:
                raise Conflict('Записът е променен. Обновете и прегледайте отново.')
            data.update(version=before['version'] + 1, updated_at=now())
            c.execute(f'UPDATE {entity} SET '+','.join(f'{k}=?' for k in data)+' WHERE id=?', (*data.values(), record_id))
        else:
            record_id = uid()
            data.update(id=record_id, version=1, source_message=message_id, created_at=now(), updated_at=now())
            c.execute(f'INSERT INTO {entity} ({",".join(data)}) VALUES ({",".join("?" for _ in data)})', tuple(data.values()))
        after = self.record(c, entity, record_id)
        c.execute('INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?)',
                  (uid(), entity, record_id, 'update' if before else 'create', encode(before) if before else None,
                   encode(after), message_id, device_id, now()))
        return after

    def message(self, c, message_id):
        row = c.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
        if row is None:
            raise ValueError('Съобщението не е намерено.')
        result = dict(row)
        for field in ('proposal', 'result'):
            result[field] = json.loads(result[field]) if result[field] else None
        result.pop('audio_path', None)
        return result

    def query(self, c, query, tz):
        records = self.records(c, query.entity)
        companies = {r['id']:r['title'] for r in self.records(c, 'companies')}
        contacts = {r['id']:r['title'] for r in self.records(c, 'contacts')}
        result = []
        for r in records:
            if r['status'] == 'archived':
                continue
            if query.status and r['status'] != query.status:
                continue
            haystack = ' '.join(str(r.get(k) or '') for k in ('title','body','email','phone'))
            haystack += ' '+companies.get(r['company_id'], '')+' '+contacts.get(r['contact_id'], '')
            if query.search.casefold() not in haystack.casefold():
                continue
            date = r['start_at'] or r['due_at']
            if query.date_from or query.date_to:
                if not date:
                    continue
                day = datetime.fromisoformat(date).astimezone(ZoneInfo(tz)).date().isoformat()
                if query.date_from and day < query.date_from or query.date_to and day > query.date_to:
                    continue
            result.append(r)
        result.sort(key=lambda r:r['start_at'] or r['due_at'] or r['updated_at'])
        return {'count':len(result), 'records':result[:100], 'truncated':len(result)>100}

    def apply_plan(self, c, message, plan, device_id):
        plan = Plan.model_validate(plan)
        if plan.intent != 'write' or message['mode'] == 'query':
            raise ValueError('Това съобщение не разрешава запис.')
        results = []
        def resolve(table, name):
            if not name:
                return None
            matches = [r for r in self.records(c, table) if r['title'].casefold() == name.strip().casefold() and r['status'] != 'archived']
            if len(matches) != 1:
                raise ValueError(f'Нужен е еднозначен запис за „{name}“. Добавете/уточнете го и анализирайте отново.')
            return matches[0]['id']
        # Resolve newly created parents before their children, within one transaction.
        order = {k:i for i,k in enumerate(KINDS)}
        for op in sorted(plan.operations, key=lambda op:order[op.entity]):
            if op.evidence.strip() not in message['text']:
                raise ValueError('Предложение без точен откъс от източника.')
            if op.action == 'update':
                if not op.target_id or not op.expected_version:
                    raise ValueError('Актуализацията изисква конкретен запис и версия.')
                previous = self.record(c, op.entity, op.target_id)
                data = {k:previous[k] for k in Record.model_fields}
                # Nullable fields not provided by the model preserve previous values.
                for k in Record.model_fields:
                    if hasattr(op,k) and getattr(op,k) is not None and (k != 'body' or op.body):
                        data[k] = getattr(op,k)
            else:
                data = {k:getattr(op,k) for k in Record.model_fields if hasattr(op,k)}
                # Never silently merge contacts by name. Block possible duplicates for review.
                if op.entity in ('companies','contacts'):
                    for r in self.records(c,op.entity):
                        same_name = r['title'].casefold() == op.title.casefold()
                        same_email = op.email and (r['email'] or '').casefold() == op.email.casefold()
                        same_phone = op.phone and ''.join(filter(str.isdigit,r['phone'] or '')) == ''.join(filter(str.isdigit,op.phone))
                        if r['status'] != 'archived' and (same_name or same_email or same_phone):
                            raise Conflict(f'Възможен дубликат: {r["title"]}. Изберете актуализация на съществуващия запис.')
            if op.company_name:
                data['company_id'] = resolve('companies',op.company_name)
            if op.contact_name:
                data['contact_id'] = resolve('contacts',op.contact_name)
                if not data.get('company_id'):
                    data['company_id'] = self.record(c,'contacts',data['contact_id'])['company_id']
            results.append(self.save(c,op.entity,data,device_id,
                                     op.target_id if op.action=='update' else None, op.expected_version,message['id']))
        return results
