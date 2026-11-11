import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import core,server


class TechnicalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=patch.object(core,'DATA',Path(self.temp.name));self.data.start()
        with server.TECH_LOCK:server.TECH_JOB.update(running=False,started_at=None,finished_at=None,error=None)
        self.client=TestClient(server.app);self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);self.data.stop();self.temp.cleanup()

    def test_refresh_requires_local_marker_and_matching_origin(self):
        self.assertEqual(self.client.post('/api/technical/refresh').status_code,403)
        r=self.client.post('/api/technical/refresh',headers={'x-workbench-action':'local-refresh','origin':'https://elsewhere.invalid'})
        self.assertEqual(r.status_code,403)

    def test_refresh_runs_once_and_rejects_concurrent_request(self):
        entered=threading.Event();release=threading.Event()
        def build():entered.set();release.wait(3)
        with patch('app.technical.build',side_effect=build) as mocked:
            try:
                r=self.client.post('/api/technical/refresh',headers={'x-workbench-action':'local-refresh'})
                self.assertEqual(r.status_code,200);self.assertTrue(entered.wait(1))
                self.assertEqual(self.client.post('/api/technical/refresh',headers={'x-workbench-action':'local-refresh'}).status_code,409)
            finally:release.set()
            for _ in range(100):
                if not self.client.get('/api/technical/status').json()['running']:break
                time.sleep(.01)
            self.assertEqual(mocked.call_count,1)
            self.assertIsNone(self.client.get('/api/technical/status').json()['error'])

    def test_source_failure_keeps_previous_result(self):
        before=self.client.get('/api/technical').content
        with patch('app.technical.build',side_effect=ValueError('upstream unavailable')):
            self.client.post('/api/technical/refresh',headers={'x-workbench-action':'local-refresh'})
            for _ in range(100):
                if not self.client.get('/api/technical/status').json()['running']:break
                time.sleep(.01)
        self.assertEqual(self.client.get('/api/technical/status').json()['error'],'upstream unavailable')
        after=self.client.get('/api/technical');self.assertEqual(after.content,before)
        self.assertEqual(after.headers['cache-control'],'no-store')
