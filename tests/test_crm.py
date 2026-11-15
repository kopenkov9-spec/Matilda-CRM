import io
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from crm.app import create_app
from crm.schema import Operation, Plan
from crm.normalize import ground_plan, local_stamp
from zoneinfo import ZoneInfo


class FakeAI:
    def available(self):
        return True

    def extract(self, message, records):
        if message['mode'] == 'query':
            return Plan(intent='query', query={'entity': 'notes'})
        return Plan(intent='write', operations=[Operation(entity='notes', title=message['text'], evidence=message['text'])])


class CRMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root, ai=FakeAI(), start_worker=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-CRM-Client': 'mestno'}

    def post(self, path, data):
        return self.client.post('/api'+path, json=data, headers=self.headers)

    def login(self):
        r = self.post('/auth/local', {'key': (self.root/'owner-key.txt').read_text()})
        self.assertEqual(r.status_code, 200, r.text)

    def message(self, text='Бележка за тест', mode='write'):
        return dict(id=str(uuid.uuid4()), text=text, mode=mode,
                    recorded_at=datetime.now(timezone.utc).isoformat(), timezone='Europe/Sofia')

    def test_auth_and_host(self):
        self.assertEqual(self.client.get('/api/records').status_code, 401)
        self.assertEqual(self.client.get('/api/health', headers={'Host':'evil.example'}).status_code, 400)
        self.assertEqual(self.post('/auth/local', {'key':'wrong'}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/api/records/notes', json={'title':'test'}).status_code, 403)
        self.assertEqual(self.client.post('/api/records/notes', json={'title':'test'}, headers={**self.headers,'Origin':'https://evil.example'}).status_code, 403)

    def test_create_and_conflict(self):
        self.login()
        r = self.post('/records/notes', {'title':'Здравей'}).json()
        self.assertEqual(r['version'], 1)
        path='/api/records/notes/'+r['id']
        self.assertEqual(self.client.put(path, json={'version':1,'data':{'title':'Промяна'}}, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.put(path, json={'version':1,'data':{'title':'Конфликт'}}, headers=self.headers).status_code, 409)
        self.assertEqual(len(self.client.get('/api/audit').json()), 2)

    def test_review_and_confirm(self):
        self.login()
        body=self.message()
        self.assertEqual(self.post('/messages', body).status_code, 202)
        self.assertEqual(self.post('/messages', body).status_code, 202)
        self.assertEqual(self.post('/messages', {**body,'text':'other'}).status_code, 409)
        self.assertTrue(self.app.state.worker.once())
        path='/messages/'+body['id']
        self.assertEqual(self.client.get('/api'+path).json()['status'], 'needs_review')
        self.assertEqual(self.client.get('/api/records').json(), [])
        self.assertEqual(self.post(path+'/confirm', {'revision':1}).status_code, 200)
        self.assertEqual(self.post(path+'/confirm', {'revision':1}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/records').json()), 1)

    def test_query_no_write(self):
        self.login()
        body=self.message('Какви бележки имам?', 'query')
        self.post('/messages',body)
        self.app.state.worker.once()
        r=self.client.get('/api/messages/'+body['id']).json()
        self.assertEqual(r['status'],'done')
        self.assertEqual(r['result']['count'],0)
        self.assertEqual(self.client.get('/api/records').json(),[])

    def test_pair_and_revoke(self):
        self.login()
        code=self.post('/devices/code',{}).json()['code']
        with TestClient(self.app) as member:
            r=member.post('/api/auth/pair',json={'code':code},headers=self.headers)
            self.assertEqual(r.status_code,200)
            device=r.json()['id']
            self.assertEqual(member.get('/api/export').status_code,403)
            self.assertEqual(member.post('/api/auth/pair',json={'code':code},headers=self.headers).status_code,403)
            self.assertEqual(self.client.delete('/api/devices/'+device,headers={**self.headers,'Content-Length':'0'}).status_code,200)
            self.assertEqual(member.get('/api/records').status_code,401)

    def test_cancel_queued_audio(self):
        self.login()
        body=self.message()
        r=self.client.post('/api/messages/audio', data={'id':body['id'],'recorded_at':body['recorded_at']},
                           files={'file':('voice.wav',io.BytesIO(b'fake'), 'audio/wav')}, headers=self.headers)
        self.assertEqual(r.status_code,202,r.text)
        self.assertEqual(len(list((self.root/'audio').glob('*'))),1)
        path='/messages/'+body['id']
        self.assertEqual(self.client.patch('/api'+path,json={'text':'edit','revision':1},headers=self.headers).status_code,409)
        self.assertEqual(self.post(path+'/cancel',{}).status_code,200)
        self.assertEqual(list((self.root/'audio').glob('*')),[])

    def test_export_consistent(self):
        self.login()
        self.post('/records/notes',{'title':'Backup test'})
        response=self.client.get('/api/export')
        self.assertEqual(response.status_code,200)
        self.assertEqual(list(self.root.glob('backup-*.sqlite3')),[])
        restored=self.root/'verify.sqlite3'
        restored.write_bytes(response.content)
        with sqlite3.connect(restored) as conn:
            self.assertEqual(conn.execute('SELECT title FROM notes').fetchone()[0],'Backup test')
        conn.close()

    def test_blank_evidence_rejected(self):
        with self.assertRaises(ValidationError):
            Operation(entity='notes',title='fake',evidence='   ')

    def test_date_grounding(self):
        message={'recorded_at':'2026-11-15T18:00:00+02:00','timezone':'Europe/Sofia','text':'Среща утре в 14:00'}
        plan=Plan(intent='write',operations=[Operation(entity='meetings',title='Среща',evidence=message['text'])])
        self.assertEqual(ground_plan(plan,message,[]).operations[0].start_at,'2026-11-16T14:00:00+02:00')


if __name__ == '__main__':
    unittest.main()
