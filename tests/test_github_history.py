import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import core,github_history

CONTENT=b'time,open,close,high,low,vol\n2024-01-02,10,11,12,9,100\n2024-01-03,11,10,12,9,80\n'
class GitHubHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(core,'DATA',Path(self.tmp.name));self.patch.start();core.init()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_pinned_blob_hash_rejects_modified_download(self):
        spec={'size':len(CONTENT),'sha':hashlib.sha1(b'blob '+str(len(CONTENT)).encode()+b'\0'+CONTENT).hexdigest()}
        self.assertEqual(github_history.verify(CONTENT,spec),hashlib.sha256(CONTENT).hexdigest())
        with self.assertRaises(ValueError):github_history.verify(CONTENT.replace(b'11',b'99'),spec)
    def test_external_prices_not_labeled_unadjusted(self):
        rows,q=github_history.parse(CONTENT,'000001.SZ','2026-11-10T07:00:00+00:00')
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['adjustment'],'source_unspecified')
        self.assertIn('adjustment_not_declared',rows[0]['flags']);self.assertEqual(core.observations('000001.SZ','1d'),[])
    def test_unknown_csv_format_rejected(self):
        with self.assertRaisesRegex(ValueError,'schema'):github_history.parse(b'date,price\n2024-01-01,1\n','000001.SZ',core.iso())
    def test_conflicting_external_dates_excluded(self):
        rows,q=github_history.parse(CONTENT+b'2024-01-02,10,10,12,9,100\n','000001.SZ',core.iso())
        self.assertEqual(len(rows),1);self.assertEqual(q['conflicting_dates'],1)
    def test_overlap_audit_detects_price_difference(self):
        batch=core.begin_batch('000001.SZ','1d','fixture',{})
        core.publish(batch,[{'日期':'2024-01-02','开盘':10,'收盘':10,'最高':12,'最低':9}])
        rows,_=github_history.parse(CONTENT,'000001.SZ',core.iso());audit=github_history.audit(rows,'000001.SZ')
        self.assertEqual(audit['compared_days'],1);self.assertEqual(audit['ohlc_mismatch_days'],1)

if __name__=='__main__':unittest.main()
