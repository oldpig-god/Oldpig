import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import unittest
from app import tencent_source,technical

class TencentEvidenceTests(unittest.TestCase):
    def test_raw_adjustment_identity_and_checksum_enforced(self):
        content=json.dumps({'code':0,'data':{'sh600519':{'qfqday':[['2025-01-02','10','11','12','9','100']], 'qt':{'sh600519':['1','贵州茅台','600519']}}}}).encode()
        evidence={'symbol':'600519.SH','adjustment':1,'responses':[{'sha256':hashlib.sha256(content).hexdigest(),'content_base64':base64.b64encode(content).decode()}]}
        rows,name=tencent_source.rows_from_evidence(evidence,'600519.SH',1,'2025-01-01','2025-01-31')
        self.assertEqual(name,'贵州茅台');self.assertEqual(rows[0][0],'2025-01-02')
        with self.assertRaises(ValueError):tencent_source.rows_from_evidence(evidence,'000001.SZ',1,'2025-01-01','2025-01-31')
        evidence['responses'][0]['sha256']='tampered'
        with self.assertRaisesRegex(ValueError,'checksum'):tencent_source.rows_from_evidence(evidence,'600519.SH',1,'2025-01-01','2025-01-31')
    def test_quotes_require_matching_identity_positive_price_and_real_timestamp(self):
        cells=['']*33;cells[1]='贵州茅台';cells[2]='600519';cells[3]='100';cells[4]='95';cells[6]='123';cells[30]='20251110150000'
        content=('v_sh600519="'+'~'.join(cells)+'";').encode('gb18030')
        result=tencent_source.parse_quotes(content,['600519.SH','000001.SZ'])
        self.assertEqual(set(result),{'600519.SH'});self.assertAlmostEqual(result['600519.SH']['pct_change'],100/95*100-100)
        cells[3]='0';content=('v_sh600519="'+'~'.join(cells)+'";').encode('gb18030')
        self.assertEqual(tencent_source.parse_quotes(content,['600519.SH']),{})
    def test_only_unaligned_trailing_dates_are_excluded(self):
        snapshot=technical.PORTABLE;manifest=json.loads((snapshot/'manifest.json').read_text());frames,quality=technical.load_frames(snapshot,manifest)
        frames=copy.deepcopy(frames);quality=copy.deepcopy(quality)
        frames[('600031.SH',1)].iloc[-1]=float('nan')
        # Use the actual input reader but change one raw response so adjusted last day is missing.
        original=(snapshot/'600031.SH.1.json').read_bytes();payload=json.loads(original);payload['data']['klines']=payload['data']['klines'][:-1];changed=json.dumps(payload).encode()
        source=next(s for s in manifest['sources'] if s['symbol']=='600031.SH' and s['adjustment']==1);source['sha256']=hashlib.sha256(changed).hexdigest()
        original_read=Path.read_bytes
        def read(path):return changed if path==snapshot/'600031.SH.1.json' else original_read(path)
        with patch.object(Path,'read_bytes',read):trimmed,quality=technical.load_frames(snapshot,manifest)
        self.assertTrue(all(f.index[-1]=='2026-11-09' for f in trimmed.values()))
        self.assertTrue(any(q.get('trailing_unaligned_excluded')==1 for q in quality))
