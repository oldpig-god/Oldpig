import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_cloud_security import CloudSecurityTests
from test_strategy import fixture
from app import strategy_service,strategy

class StrategyCloudTests(CloudSecurityTests):
    def setUp(self):
        super().setUp()
        self.store.list_keys=lambda prefix:sorted([k for k in self.store.values if k.startswith(prefix)],reverse=True)
        self.bundle=fixture()
        self.source=patch.object(strategy_service.strategy_data,'acquire',return_value=self.bundle);self.source.start()
    def tearDown(self):self.source.stop();super().tearDown()
    def create(self,**extra):
        return self.client.post('/api/strategy/run',json=dict(symbol='600031.SH',random_runs=20,**extra),headers={'x-workbench-action':'local-refresh'})
    def test_strategy_auth_validation_and_origin(self):
        self.assertEqual(self.create().status_code,401);self.login()
        self.assertEqual(self.create(fast=100,slow=60).status_code,400)
        self.assertEqual(self.client.post('/api/strategy/run',json={},headers={'Origin':'https://evil.example','x-workbench-action':'local-refresh'}).status_code,403)
        self.assertEqual(self.client.get('/api/strategy/experiments/../download').status_code,404)
    def test_run_download_replay_account_isolation(self):
        self.login();response=self.create();self.assertEqual(response.status_code,200,response.text)
        result=response.json();eid=result['experiment']['id'];base='/api/strategy/experiments/'+eid
        self.assertEqual(self.client.get('/api/strategy/experiments').json()['parameter_evaluations'],9)
        downloaded=self.client.get(base+'/download').json();self.assertEqual(downloaded['data'],self.bundle)
        replay=self.client.post(base+'/replay',json={},headers={'x-workbench-action':'local-refresh'})
        self.assertEqual(replay.status_code,200,replay.text);self.assertTrue(replay.json()['identical'])
        self.login('bob','other');self.assertEqual(self.client.get(base).status_code,404)
        self.assertEqual(self.client.get(base+'/download').status_code,404)
        self.assertEqual(self.client.get('/api/strategy/experiments').json()['records'],[])
    def test_save_failure_does_not_claim_completion(self):
        self.login();self.store.fail_write=True;response=self.create();self.assertEqual(response.status_code,502)
        self.store.fail_write=False;self.assertEqual(self.client.get('/api/strategy/experiments').json()['completed_experiments'],0)
    def test_replay_checks_frozen_data_and_code(self):
        self.login();r=self.create().json();eid=r['experiment']['id'];base='/api/strategy/experiments/'+eid
        key=strategy_service.prefix('alice')+eid+'.json.gz'
        self.store.values[key]['data']['calendar'][0]='2001-01-01'
        self.assertEqual(self.client.post(base+'/replay',json={},headers={'x-workbench-action':'local-refresh'}).status_code,422)
        self.store.values[key]['result']['code_hash']='different'
        self.assertEqual(self.client.post(base+'/replay',json={},headers={'x-workbench-action':'local-refresh'}).status_code,409)
    def test_no_credentials_in_results(self):
        self.login();r=self.create();self.assertNotIn('test-secret',r.text);self.assertNotIn('test-signing-key',r.text)

if __name__=='__main__':unittest.main()
