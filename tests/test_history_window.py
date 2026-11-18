import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from app import core,collector

class HistoryWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(core,'DATA',Path(self.tmp.name));self.patch.start();core.init()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_five_calendar_years_and_leap_day(self):
        self.assertEqual(core.history_start(datetime.fromisoformat('2026-11-10T07:00:00+00:00')),'2021-11-10')
        self.assertEqual(core.history_start(datetime.fromisoformat('2024-02-29T07:00:00+00:00')),'2019-02-28')
        self.assertEqual(core.history_start(datetime.fromisoformat('2026-11-09T17:00:00+00:00')),'2021-11-10')
    def test_full_requests_use_five_year_start(self):
        with patch.object(core,'now',return_value=datetime.fromisoformat('2026-11-10T07:00:00+00:00')):
            _,params=collector.parameters('510300.SH','1d',full=True)
        self.assertEqual(params['start_date'],'20211110');self.assertEqual(params['end_date'],'20261109')
    def test_boundary_included_older_record_excluded(self):
        base={'日期':'2021-11-10','开盘':10,'收盘':11,'最高':12,'最低':9}
        self.assertIsNotNone(core.normalize(base,'510300.SH','1d','2026-11-10T07:00:00+00:00')[0])
        base['日期']='2021-11-09'
        value,flags=core.normalize(base,'510300.SH','1d','2026-11-10T07:00:00+00:00')
        self.assertIsNone(value);self.assertIn('outside_research_window',flags)
    def test_columnar_prices_and_raw_evidence_roundtrip(self):
        b=core.begin_batch('510300.SH','1d','test',{})
        core.publish(b,[{'日期':'2026-11-09','开盘':10,'收盘':11,'最高':12,'最低':9}],'2026-11-10T07:00:00+00:00')
        with core.db() as c:
            bar=c.execute('SELECT * FROM bars').fetchone();batch=c.execute('SELECT * FROM batches').fetchone()
        self.assertEqual((bar['open'],bar['high'],bar['low'],bar['close'],bar['extra']),(10,12,9,11,None))
        self.assertEqual(core.read_raw(core.DATA/batch['raw_path'])['rows'][0]['收盘'],11)
        rec=core.observations('510300.SH','1d')[0]
        self.assertEqual(rec['close'],11);self.assertEqual(rec['event_time'],'2026-11-09T15:00:00+08:00');self.assertEqual(rec['batch_id'],b)

if __name__=='__main__':unittest.main()
