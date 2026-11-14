import unittest,copy
from unittest.mock import patch
import pandas as pd
from app import sector_comparison as s
from test_comparison import fixture
from test_data_health import AtomicStore
class SectorTests(unittest.TestCase):
 def test_positive_negative_covariance(self):
  snap,_=fixture();r=s.compute(snap,'a','negative',120,20);self.assertAlmostEqual(r['metrics']['correlation'],-1);self.assertLess(r['metrics']['covariance'],0);self.assertEqual(r['metrics']['n'],120);self.assertEqual(r['negative_rolling_share'],1)
 def test_normalized_common_anchor(self):
  snap,_=fixture();r=s.compute(snap,'a','b',60,20);self.assertEqual(r['normalized'][0]['a'],100);self.assertEqual(r['normalized'][0]['b'],100);self.assertEqual(len(r['normalized']),61)
 def test_gap_does_not_bridge_returns_or_chart(self):
  snap,_=fixture();snap['assets']['a']['rows'].pop(90);r=s.compute(snap,'a','b',0,60);self.assertEqual(r['metrics']['n'],178);self.assertEqual(len(r['missing']),2);self.assertIsNone(r['normalized'][90]['a']);self.assertIsNotNone(r['normalized'][91]['a']);self.assertIsNone(r['rolling'][90]['value'])
 def test_no_benchmark_required(self):
  snap,_=fixture();del snap['assets']['510300.SH'];self.assertEqual(s.compute(snap,'a','b')['metrics']['n'],180)
 def test_short_sample_constant_and_same(self):
  snap,_=fixture();self.assertIsNone(s.compute(snap,'a','flat')['metrics']['correlation']);self.assertIn('两边为同一板块',' '.join(s.compute(snap,'a','a')['warnings']))
  for x in snap['assets'].values():x['rows']=x['rows'][:20]
  with self.assertRaisesRegex(ValueError,'至少需要30'):s.compute(snap,'a','b')
 def test_bad_price_duplicate_and_calendar(self):
  snap,_=fixture()
  for mode in ['price','duplicate','calendar']:
   rows=copy.deepcopy(snap['assets']['a']['rows'])
   if mode=='price':rows[1][2]=-1
   if mode=='duplicate':rows.append(rows[0])
   if mode=='calendar':rows[0][0]='1999-01-01'
   with self.assertRaises(ValueError):s.validate_rows(rows,snap['calendar'])
 def test_stale_and_deterministic(self):
  snap,_=fixture();snap['assets']['a']['rows'].pop();r=s.compute(snap,'a','b');self.assertIn('未覆盖应有交易日',' '.join(r['warnings']));self.assertEqual(r,s.compute(copy.deepcopy(snap),'a','b'))
 def test_catalog_error_keeps_previous_and_marks_stale(self):
  store=AtomicStore();store.put_json('system/industry-ths/catalog.json.gz',{'date':'2000-01-01','items':[]})
  with patch.object(s,'fetch_catalog',side_effect=TimeoutError()):self.assertTrue(s.catalog(store)['stale'])
