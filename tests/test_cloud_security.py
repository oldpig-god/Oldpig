"""Cloud deployment boundary tests: authentication, isolation and durable writes."""
import base64
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import core

class MemoryStore:
    def __init__(self):self.values={};self.fail_write=False
    def get_json(self,key,optional=False):return self.values.get(key)
    def get(self,key,optional=False):return self.values.get(key)
    def put_json(self,key,value):
        if self.fail_write:raise RuntimeError('storage unavailable')
        self.values[key]=value
    def put(self,key,value):self.values[key]=value

class CloudSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=patch.object(core,'DATA',Path(self.temp.name));self.data.start();core.init()
        self.env=patch.dict(os.environ,{'CLOUDBASE_ENV_ID':'test-env','CLOUDBASE_APIKEY':'test-secret','FINANCE_SESSION_KEY':'test-signing-key','FINANCE_CLOUD_USERS_B64':base64.b64encode(json.dumps({'alice':{'salt':'salt','hash':hashlib.pbkdf2_hmac('sha256',b'correct',b'salt',200000).hex()},'bob':{'salt':'salt','hash':hashlib.pbkdf2_hmac('sha256',b'other',b'salt',200000).hex()}}).encode()).decode(),'FINANCE_COOKIE_SECURE':'0','FINANCE_PUBLIC_ORIGIN':'http://testserver'});self.env.start()
        from app import cloud_server
        self.module=cloud_server
        cloud_server.ACCOUNTS=json.loads(base64.b64decode(os.environ['FINANCE_CLOUD_USERS_B64']));cloud_server.SIGNING_KEY='test-signing-key'
        cloud_server.CACHE.clear();cloud_server.LOGIN_FAILURES.clear()
        self.store=MemoryStore();self.store.values['system/technical/analysis.json.gz']={'watchlist':{'600031.SH':'三一重工'},'owner':'default'}
        self.stub=patch.object(cloud_server,'STORE',self.store);self.stub.start()
        self.client=TestClient(cloud_server.app)
    def tearDown(self):
        self.client.close();self.stub.stop();self.env.stop();self.data.stop();self.temp.cleanup()
    def login(self,user='alice',password='correct'):
        response=self.client.post('/login',json={'username':user,'password':password});self.assertEqual(response.status_code,200);return response
    def test_auth_blocks_api_downloads_and_pages(self):
        self.assertEqual(self.client.get('/api/technical').status_code,401)
        self.assertEqual(self.client.get('/static/technical/analysis.json').status_code,401)
        self.assertEqual(self.client.get('/static/research/analysis.json').status_code,401)
        self.assertEqual(self.client.get('/',follow_redirects=False).status_code,303)
        self.assertEqual(self.client.get('/healthz').json(),{'ok':True})
    def test_invalid_password_signed_cookie_and_logout(self):
        self.assertEqual(self.client.post('/login',json={'username':'alice','password':'wrong'}).status_code,401)
        response=self.login();cookie=response.headers['set-cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=lax',cookie)
        self.assertEqual(self.client.get('/api/session').json()['username'],'alice')
        self.assertIsNone(self.module.session_user(self.module.token_for('alice')+'tampered'))
        self.assertEqual(self.client.post('/logout',headers={'x-workbench-action':'local-refresh'}).status_code,200)
        self.assertEqual(self.client.get('/api/session').status_code,401)
    def test_user_analysis_isolation(self):
        self.store.values.update({'users/alice/technical/current.json.gz':{'analysis':'alice-result'},'alice-result':{'owner':'alice'},'users/bob/technical/current.json.gz':{'analysis':'bob-result'},'bob-result':{'owner':'bob'}})
        self.login();self.assertEqual(self.client.get('/api/technical').json()['owner'],'alice')
        self.login('bob','other');self.assertEqual(self.client.get('/api/technical').json()['owner'],'bob')
    def test_cloud_write_failure_keeps_previous_analysis_pointer(self):
        self.login();self.store.values['users/alice/technical/current.json.gz']={'analysis':'previous'};self.store.values['previous']={'owner':'previous'}
        self.store.fail_write=True
        def fake_build(output,watchlist):
            (output/'evidence.zip').write_bytes(b'evidence');return {'generated_at':'now','watchlist':watchlist}
        with patch.object(self.module.technical,'build',side_effect=fake_build):
            r=self.client.post('/api/technical/refresh',json={'symbols':['600031.SH']},headers={'x-workbench-action':'local-refresh'})
        self.assertEqual(r.status_code,502)
        self.assertEqual(self.store.values['users/alice/technical/current.json.gz'],{'analysis':'previous'})
        self.assertEqual(self.client.get('/api/technical').json()['owner'],'previous')
    def test_cross_origin_write_rejected(self):
        self.login()
        r=self.client.post('/api/technical/refresh',json={'symbols':['600031.SH']},headers={'x-workbench-action':'local-refresh','Origin':'https://evil.invalid'})
        self.assertEqual(r.status_code,403)
    def test_platform_headers_never_reflected(self):
        self.login()
        r=self.client.get('/api/session',headers={'x-cloudbase-context':'secret-platform-value'})
        self.assertNotIn('secret-platform-value',r.text);self.assertNotIn('test-secret',r.text)

    def test_cloud_backfill_really_refreshes_selected_daily_history(self):
        self.login()
        with patch.object(self.module,'refresh_series',return_value={'mode':'cloud','updated':True}) as refresh:
            r=self.client.post('/api/backfill',json={'symbols':['600519.SH'],'intervals':['1m']},headers={'x-workbench-action':'local-refresh'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(refresh.call_args.args[1].symbols,['600519.SH'])
        self.assertEqual(refresh.call_args.args[1].intervals,['1d'])
