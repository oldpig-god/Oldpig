import unittest,copy,json
import numpy as np
import pandas as pd
from app.comparison import compute

def fixture():
 dates=pd.bdate_range('2025-01-01',periods=181).strftime('%Y-%m-%d').tolist();rng=np.random.default_rng(7);r=rng.normal(0,.012,180);market=rng.normal(0,.01,180)
 assets={}
 for symbol,rets in [('a',r),('b',2*r),('negative',-r),('510300.SH',market),('flat',np.zeros(180))]:
  prices=np.r_[10,10*np.cumprod(1+rets)];assets[symbol]={'name':symbol,'rows':[[d,p,p,p,p,100] for d,p in zip(dates,prices)]}
 return {'calendar':dates,'assets':assets,'end':dates[-1],'fetched_at':'frozen'},dict(left={'name':'A','symbols':['a']},right={'name':'B','symbols':['b']},lookback=0,rolling=60)
class ComparisonTests(unittest.TestCase):
 def test_covariance_units_and_equal_weight(self):
  s,c=fixture();r=compute(s,c);self.assertAlmostEqual(r['metrics']['correlation'],1);self.assertAlmostEqual(r['metrics']['covariance'],2*r['metrics']['variance_a']);self.assertAlmostEqual(r['metrics']['annualized_covariance'],252*r['metrics']['covariance'])
  c['left']['symbols']=['a','b'];r=compute(s,c);self.assertAlmostEqual(r['metrics']['variance_a']*4/9,compute(s,fixture()[1])['metrics']['variance_a'])
 def test_negative_same_and_constant(self):
  s,c=fixture();c['right']['symbols']=['negative'];self.assertAlmostEqual(compute(s,c)['metrics']['correlation'],-1)
  c['right']['symbols']=['a'];self.assertIn('两边成分相同',' '.join(compute(s,c)['warnings']))
  c['right']['symbols']=['flat'];self.assertIsNone(compute(s,c)['metrics']['correlation'])
 def test_missing_never_bridged_and_rolling_resets(self):
  s,c=fixture();s['assets']['a']['rows'].pop(90);r=compute(s,c)
  self.assertEqual(r['metrics']['n'],178);self.assertIsNone(r['rolling'][90]['value']);self.assertIsNone(r['rolling'][148]['value']);self.assertEqual(len(r['missing']),2)
 def test_zero_volume_and_short_history(self):
  s,c=fixture();s['assets']['a']['rows'][50][-1]=0;self.assertEqual(compute(s,c)['metrics']['n'],178)
  for item in s['assets'].values():item['rows']=item['rows'][:20]
  with self.assertRaises(ValueError):compute(s,c)
 def test_benchmark_residual_not_spurious(self):
  s,c=fixture();c['left']['symbols']=['510300.SH'];self.assertIsNone(compute(s,c)['metrics']['residual_correlation'])
 def test_frozen_recompute_and_window(self):
  s,c=fixture();self.assertEqual(compute(s,c),compute(json.loads(json.dumps(s,sort_keys=True)),c));c['lookback']=60;self.assertEqual(compute(s,c)['metrics']['n'],60)
 def test_invalid_prices_rejected(self):
  s,c=fixture();s['assets']['a']['rows'][10][2]=0
  with self.assertRaises(ValueError):compute(s,c)
