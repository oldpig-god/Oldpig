import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from app import core,collector,github_history

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
    def test_github_data_uses_same_window(self):
        content=b'time,open,close,high,low,vol\n2020-01-02,10,11,12,9,100\n2021-11-10,10,11,12,9,100\n'
        rows,quality=github_history.parse(content,'000001.SZ','2026-11-10T07:00:00+00:00')
        self.assertEqual(len(rows),1);self.assertEqual(quality['excluded_before_window'],1)
        self.assertTrue(rows[0]['event_time'].startswith('2021-11-10'))
    def test_compressed_prices_and_raw_evidence_roundtrip(self):
        b=core.begin_batch('510300.SH','1d','test',{})
        core.publish(b,[{'日期':'2026-11-09','开盘':10,'收盘':11,'最高':12,'最低':9}],'2026-11-10T07:00:00+00:00')
        with core.db() as c:
            payload=c.execute('SELECT payload FROM observations').fetchone()[0];batch=c.execute('SELECT * FROM batches').fetchone()
        self.assertTrue(payload.startswith(b'Z1'));self.assertEqual(core.unpack_payload(payload)['close'],11)
        self.assertEqual(core.read_raw(core.DATA/batch['raw_path'])['rows'][0]['收盘'],11)
        self.assertEqual(core.observations('510300.SH','1d')[0]['close'],11)

if __name__=='__main__':unittest.main()
