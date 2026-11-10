import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import core, collector, server

FETCH='2026-11-10T06:45:00+00:00'
SYMBOL='510300.SH'
def row(**changes):
    r={'日期':'2026-11-09','时间':'2026-11-10 14:40:00','开盘':4.1,'最高':4.3,'最低':4.0,'收盘':4.2,'成交量':120,'成交额':504}
    r.update(changes)
    return r

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(core,'DATA',Path(self.temp.name));self.patch.start();core.init()
        with core.db() as c:
            c.execute('INSERT INTO metadata VALUES(?,?)',('calendar',core.dumps({'dates':['2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-16']})))
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def publish(self,rows,interval='1d'):
        b=core.begin_batch(SYMBOL,interval,'fixture',{})
        return core.publish(b,rows,FETCH)
    def test_trends_keep_price_without_fabricating_ohlc(self):
        value,flags=core.normalize(row(开盘=0),SYMBOL,'1m',FETCH)
        self.assertEqual(value['close'],4.2)
        self.assertNotIn('open',value);self.assertNotIn('high',value);self.assertNotIn('low',value)
        self.assertIn('open_absent_in_trend_payload',flags)
        self.assertEqual(value['volume_unit'],'source_unverified')
    def test_invalid_prices_and_ohlc_rejected(self):
        for r in [row(收盘=float('nan')),row(收盘=float('inf')),row(最低=4.4),row(最高=0)]:
            self.assertIsNone(core.normalize(r,SYMBOL,'1d',FETCH)[0])
    def test_future_unfinalized_and_non_session_rows_rejected(self):
        for r,interval in [(row(时间='2026-11-10 14:50:00'),'1m'),(row(日期='2026-11-10'),'1d'),(row(时间='2026-11-10 12:30:00'),'5m')]:
            self.assertIsNone(core.normalize(r,SYMBOL,interval,FETCH)[0])
    def test_duplicate_fetches_and_revisions_with_immutable_raw(self):
        first=self.publish([row()]);again=self.publish([row(),row()]);changed=self.publish([row(收盘=4.25)])
        self.assertEqual((first['changed'],again['changed'],changed['changed']),(1,0,1))
        with core.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM observations').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM revisions').fetchone()[0],2)
            batches=c.execute('SELECT * FROM batches').fetchall()
        for batch in batches:
            raw=(core.DATA/batch['raw_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),batch['raw_sha256'])
        with self.assertRaises(FileExistsError):core.publish(first['batch_id'],[row()],FETCH)
    def test_conflicting_timestamps_are_quarantined(self):
        result=self.publish([row(),row(收盘=4.25)])
        self.assertEqual(result['status'],'quarantined');self.assertEqual(result['accepted'],0)
        self.assertEqual(core.observations(SYMBOL,'1d'),[])
    def test_failed_empty_partial_batches_do_not_advance_cursor(self):
        self.publish([row()])
        self.publish([])
        self.publish([row(日期='2026-11-10'),row(日期='2026-11-06')])
        failed=core.begin_batch(SYMBOL,'1d','fixture',{});core.fail_batch(failed,'timeout')
        with core.db() as c:
            watermark=c.execute('SELECT event_time FROM watermarks').fetchone()[0]
        self.assertTrue(watermark.startswith('2026-11-09'))
        self.assertEqual(len(core.observations(SYMBOL,'1d')),2)
    def test_freshness_honors_lunch_close_and_unknown_calendar(self):
        def fresh(event,at):
            return core.freshness({'event_time':event+'+08:00','fetched_at':FETCH},'1m',datetime.fromisoformat(at+'+08:00'))['state']
        self.assertEqual(fresh('2026-11-10T11:30:00','2026-11-10T12:40:00'),'closed')
        self.assertEqual(fresh('2026-11-10T11:00:00','2026-11-10T12:40:00'),'stale')
        self.assertEqual(fresh('2026-11-10T15:00:00','2026-11-10T19:00:00'),'closed')
        self.assertEqual(fresh('2026-11-10T14:44:00','2026-11-10T14:45:00'),'recent')
        self.assertEqual(fresh('2026-11-10T15:00:00','2027-01-01T12:00:00'),'unknown')
    def test_daily_requests_exclude_today_during_session(self):
        with patch.object(core,'now',return_value=datetime.fromisoformat(FETCH)):
            _,params=collector.parameters(SYMBOL,'1d')
        self.assertEqual(params['end_date'],'20261109')
    def test_provider_failures_preserve_saved_data(self):
        self.publish([row()])
        with patch.object(collector,'fetch',side_effect=TimeoutError('source timeout')),patch.object(collector.time,'sleep'):
            result=collector.collect([SYMBOL],['1d'])
        self.assertEqual(result['results'][0]['status'],'error')
        self.assertEqual(len(core.observations(SYMBOL,'1d')),1)
    def test_api_validation_and_batch_evidence(self):
        result=self.publish([row()])
        with TestClient(server.app) as client:
            self.assertEqual(client.get('/api/series/UNKNOWN').status_code,404)
            self.assertEqual(client.get('/api/series/'+SYMBOL+'?interval=bad').status_code,400)
            self.assertEqual(client.post('/api/refresh',json={}).status_code,403)
            headers={'x-workbench-action':'local-refresh','origin':'https://other.example'}
            self.assertEqual(client.post('/api/refresh',json={},headers=headers).status_code,403)
            self.assertEqual(client.get('/api/batches/'+result['batch_id']).json()['raw_sample'][0]['收盘'],4.2)
            self.assertEqual(client.get('/api/series/'+SYMBOL).json()['records'][0]['close'],4.2)
    def test_daily_completion_marker_only_after_success(self):
        import threading
        ended=threading.Event()
        original_thread=threading.Thread
        def immediate_thread(target,**kwargs):
            def wrapped():
                try:target()
                finally:ended.set()
            return original_thread(target=wrapped,**kwargs)
        server.JOB['running']=False;server.AUTO['daily_date']=None
        with patch.object(collector,'collect',return_value={'status':'done','results':[{'status':'error'}]}),patch.object(server.threading,'Thread',side_effect=immediate_thread):
            server.start_job(['1d'],[SYMBOL],('daily_date','2026-11-10'));self.assertTrue(ended.wait(3))
        self.assertIsNone(server.AUTO['daily_date'])
        server.JOB.update(running=False,error=None)

if __name__=='__main__':unittest.main()
