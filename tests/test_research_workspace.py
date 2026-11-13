import copy,unittest
from unittest.mock import patch
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import research_cards as c,research_workspace as w
from test_data_health import AtomicStore

def snapshot(n=300):
 days=pd.bdate_range('2024-01-01',periods=n).strftime('%Y-%m-%d').tolist()
 def asset(rate):return dict(name='测试证券',rows=[[d,100+i*rate,100+i*rate,101+i*rate,99+i*rate,1000] for i,d in enumerate(days)])
 return dict(calendar=days,start=days[0],end=days[-1],fetched_at='fixed',assets={'600031.SH':asset(1),c.BENCHMARK:asset(.1)})

class Cards(unittest.TestCase):
 def test_direction_relative_and_evidence(self):
  r=c.build(snapshot(),'600031.SH');self.assertTrue(r['usable']);self.assertEqual(r['states']['trend'],'up');self.assertEqual(r['states']['relative'],'outperform')
  self.assertEqual(r['items'][0]['evidence']['required'],65)
  self.assertEqual(r['items'][2]['evidence']['start'],snapshot()['calendar'][-252])
 def test_short_sample_unknown(self):
  r=c.build(snapshot(10),'600031.SH');self.assertEqual(set(r['states'].values()),{'unknown'})
 def test_stale_suppresses_all_judgments(self):
  s=snapshot();s['assets']['600031.SH']['rows'].pop();r=c.build(s,'600031.SH');self.assertFalse(r['usable']);self.assertEqual(set(r['states'].values()),{'unknown'});self.assertIsNone(r['chart'][-1]['close'])
 def test_gap_resets_indicators_and_relative(self):
  s=snapshot();del s['assets']['600031.SH']['rows'][-8];r=c.build(s,'600031.SH');self.assertEqual(r['quality']['consecutive_sessions'],7);self.assertEqual(set(r['states'].values()),{'unknown'})
 def test_missing_benchmark_does_not_invent_relative(self):
  s=snapshot();s['assets'][c.BENCHMARK]['rows'].pop();r=c.build(s,'600031.SH');self.assertEqual(r['states']['relative'],'unknown');self.assertEqual(r['states']['trend'],'up')
 def test_duplicate_and_invalid_prices_rejected(self):
  for mode in ['duplicate','invalid']:
   s=snapshot()
   if mode=='duplicate':s['assets']['600031.SH']['rows'].append(s['assets']['600031.SH']['rows'][-1])
   else:s['assets']['600031.SH']['rows'][-1][2]=-1
   with self.assertRaises(ValueError):c.build(s,'600031.SH')
 def test_suspension_zero_volume_not_filled(self):
  s=snapshot();s['assets']['600031.SH']['rows'][-1][-1]=0;r=c.build(s,'600031.SH');self.assertFalse(r['usable'])
 def test_deterministic_hash_and_no_future_change(self):
  a=snapshot();b=copy.deepcopy(a);b['assets']['600031.SH']['rows'][-1][1:5]=[1000,1000,1001,999]
  ra=c.build(a,'600031.SH');rb=c.build(b,'600031.SH');self.assertEqual(ra['chart'][:-1],rb['chart'][:-1]);self.assertNotEqual(ra['data_hash'],rb['data_hash']);self.assertEqual(ra['data_hash'],c.build(a,'600031.SH')['data_hash'])
 def test_transitions_deduplicate_and_combine(self):
  card=c.build(snapshot(),'600031.SH');base,changes=c.transition(None,card);self.assertEqual(changes,[]);self.assertEqual(c.transition(base,card)[1],[])
  card['states'].update(trend='down',momentum='weak',volatility='high');base2,changes=c.transition(base,card);self.assertEqual({x['kind'] for x in changes},{'risk','conditions'});self.assertEqual(c.transition(base2,card)[1],[])
  card['usable']=False;card['states']={k:'unknown' for k in card['states']};bad,changes=c.transition(base2,card);self.assertEqual([x['kind'] for x in changes],['data']);self.assertEqual(c.transition(bad,card)[1],[])

class Workspace(unittest.TestCase):
 def setUp(self):
  self.inventory=patch.object(w.core,'universe',return_value={'600031.SH':{'name':'三一重工'}});self.inventory.start();self.addCleanup(self.inventory.stop)
  self.store=AtomicStore();app=FastAPI();app.include_router(w.routes(lambda:self.store,lambda r:r.headers.get('user','a'),lambda r:None));self.client=TestClient(app)
 def test_account_isolation_settings_and_stale_revision(self):
  data=self.client.get('/api/research-workspace').json();body={k:data[k] for k in ['revision','groups','settings']};body['groups']=[];body['settings']['range']=250
  self.assertEqual(self.client.put('/api/research-workspace',json=body).status_code,200)
  self.assertEqual(self.client.put('/api/research-workspace',json=body).status_code,409)
  self.assertEqual(self.client.get('/api/research-workspace').json()['settings']['range'],250)
  self.assertEqual(self.client.get('/api/research-workspace',headers={'user':'b'}).json()['revision'],0)
 def test_atomic_revision(self):
  initial=w.state(self.store,'a');w.commit(self.store,'a',initial,initial)
  with self.assertRaises(Exception):w.commit(self.store,'a',initial,initial)
 def test_repeated_check_no_duplicate_alert(self):
  card=c.build(snapshot(),'600031.SH');w.observe(self.store,'a',[card]);card['states']['trend']='down';w.observe(self.store,'a',[card]);w.observe(self.store,'a',[card]);self.assertEqual(len(w.state(self.store,'a')['alerts']),1)
  other=self.client.post('/api/research-workspace/ack',headers={'user':'b'},json={'ids':[w.state(self.store,'a')['alerts'][0]['id']]});self.assertEqual(other.status_code,200);self.assertFalse(w.state(self.store,'a')['alerts'][0]['read'])
 def test_bad_chart_range(self):
  self.assertEqual(self.client.put('/api/research-workspace',json={'revision':0,'groups':[],'settings':{'range':9}}).status_code,400)

 def test_incomplete_price_cache_retries_until_target(self):
  self.store.put_json('system/comparison-prices/day/s.json',{'rows':[['2026-11-11',1]]})
  proxy=w.CompletePriceCache(self.store,'2026-11-12')
  self.assertIsNone(proxy.get_json('system/comparison-prices/day/s.json'))
  proxy.put_json('system/comparison-prices/day/s.json',{'rows':[['2026-11-12',1]]})
  self.assertIsNotNone(proxy.get_json('system/comparison-prices/day/s.json'))
 def test_evaluate_source_error_explicit_and_not_cached(self):
  with patch.object(w.security_research,'resolve',return_value={'symbol':'600031.SH'}),patch.object(w.daily_sync,'freshness',return_value={'expected_date':'2026-11-12'}),patch.object(w.core,'calendar_dates',return_value=[]),patch.object(w.core,'instrument',return_value={'name':'三一重工'}),patch.object(w.comparison,'acquire',side_effect=TimeoutError('timed out')):
   response=self.client.post('/api/research-workspace/evaluate',json={'symbols':['600031.SH']})
  self.assertEqual(response.status_code,200);r=response.json();self.assertFalse(r['cards'][0]['usable']);self.assertEqual(r['errors'][0]['error_category'],'timeout');self.assertFalse(self.store.list_keys('system/research-cards/'))
