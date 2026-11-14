import copy,math,tempfile,unittest
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import industry_discovery as d,portfolio_risk as risk
from test_data_health import AtomicStore

class Memory(AtomicStore):
 def list_keys(self,prefix):return [k for k in self.values if k.startswith(prefix)]


def sample():
 dates=[(datetime(2025,1,1)+timedelta(days=i)).date().isoformat() for i in range(311)]
 profiles={}
 for code,sign in [('THS111111',1),('THS222222',-1),('THS333333',1)]:
  price=100;rows=[]
  for i,date in enumerate(dates):
   price*=1+sign*(.005*math.sin(i*.7)+.002)
   rows.append([date,price,price,price,price,100])
  profiles[code]=dict(name=code,last_date=dates[-1],rows=rows)
 return profiles,dates

class DiscoveryTests(unittest.TestCase):
 def test_pair_count_and_symmetric_covariance(self):
  p,c=sample();r=d.build(p,c,c[-1],3);self.assertEqual(r['computed_pairs'],3);a=r['pairs'][0];self.assertAlmostEqual(a['windows']['250']['correlation'],-1);self.assertEqual(a['formation']['250']['n'],250);self.assertEqual(a['validation']['n'],60)
 def test_holdout_never_changes_formation(self):
  p,c=sample();before=d.build(p,c,c[-1],3)['pairs'][0]
  for i,row in enumerate(p['THS222222']['rows'][-60:]):row[1:5]=[row[2]*(1+i*.01)]*4
  after=d.build(p,c,c[-1],3)['pairs'][0]
  self.assertEqual(before['formation'],after['formation']);self.assertNotEqual(before['validation'],after['validation'])
 def test_missing_does_not_bridge(self):
  p,c=sample();rows=p['THS111111']['rows'];rows.pop(120);r=d.returns(rows,c);self.assertIsNone(r[119]);self.assertIsNone(r[120]);self.assertEqual(sum(x is None for x in r),2)
 def test_constant_short_and_coverage(self):
  self.assertIsNone(d.stats([0]*60,[1]*60)['correlation']);self.assertIsNone(d.stats([1]*20,[2]*20)['covariance'])
  p,c=sample();del p['THS333333'];r=d.build(p,c,c[-1],3,[{'code':'THS333333'}]);self.assertEqual(r['covered_industries'],2);self.assertEqual(r['possible_pairs'],3);self.assertEqual(r['computed_pairs'],1)
 def test_watch_transitions_deduplicated_and_risk_change(self):
  p,c=sample();pair=d.build(p,c,c[-1],3)['pairs'][0];w=dict(id='one',window=250,alerts=[])
  w=d.evaluate_watch(w,pair,c[-1]);self.assertEqual(w['alerts'],[])
  pair=copy.deepcopy(pair);pair['windows']['250']['correlation']=.4
  w=d.evaluate_watch(w,pair,'2027-01-01');self.assertEqual(len(w['alerts']),1);w=d.evaluate_watch(w,pair,'2027-01-01');self.assertEqual(len(w['alerts']),1)
  pair['windows']['250']['volatility_a']*=1.3;w=d.evaluate_watch(w,pair,'2027-01-02');self.assertEqual(len(w['alerts']),2)
 def test_durable_resume_and_failed_history(self):
  store=AtomicStore();calendar=['2026-11-12','2026-11-13'];item=dict(code='THS111111',name='A');calls=[]
  def load(store,code,year,*args):
   calls.append(year)
   if year==2022:raise TimeoutError()
   return {'rows':[]}
  with patch.object(d.core,'history_start',return_value='2021-11-13'),patch.object(d.industry_ths,'fetch_catalog',return_value={'items':[item]}),patch.object(d.industry_ths,'load_year',side_effect=load):
   d.sync_tick(store,calendar[-1],calendar);s=store.get_json(d.STATE);self.assertEqual(s['jobs'][item['code']]['year'],2022);self.assertEqual(s['jobs'][item['code']]['attempts'],1)
  s['jobs'][item['code']]['retry_at']=0;store.put_json(d.STATE,s)
  def valid(store,code,year,*args):return {'rows':[[calendar[-1],1,1,1,1,1]] if year==2026 else []}
  with patch.object(d.core,'history_start',return_value='2021-11-13'),patch.object(d.industry_ths,'load_year',side_effect=valid):
   r=d.sync_tick(store,calendar[-1],calendar);self.assertTrue(r['complete']);self.assertEqual(r['current'],1)
 def test_pending_unstarted_work_is_due(self):
  store=AtomicStore();store.put_json(d.STATE,dict(target='2026-11-13',complete=False,retry_at=0));self.assertTrue(d.due(store,'2026-11-13',1))

class PortfolioTests(unittest.TestCase):
 def test_endpoints_and_cost_hand_calculation(self):
  obs=[dict(date=str(i),a=.01,b=-.005) for i in range(30)]
  p=risk.calculate(obs,.5,10);expected=(.5*1.01**30+.5*.995**30)*.999**2
  self.assertAlmostEqual(p['series'][-1]['portfolio'],expected);self.assertAlmostEqual(p['metrics']['portfolio']['total_return'],expected-1)
  for weight,key in [(1,'a'),(0,'b')]:
   p=risk.calculate(obs,weight,10);self.assertEqual(p['metrics']['portfolio'],p['metrics'][key])
 def test_missing_is_visible_and_no_compounding_across_gap(self):
  obs=[dict(date=str(i),a=.01,b=.01) for i in range(30)];obs[5]['a']=None;self.assertFalse(risk.calculate(obs)['available'])
 def test_drawdown_includes_start(self):
  s=risk.summarize([1,.8,.9,.7]);self.assertAlmostEqual(s['max_drawdown'],-.3)

class DiscoveryApiTests(unittest.TestCase):
 def setUp(self):
  self.calendar_patch=patch.object(d.core,'calendar_dates',return_value=['2026-11-13','2026-11-16']);self.calendar_patch.start();self.addCleanup(self.calendar_patch.stop);self.fresh_patch=patch('app.daily_sync.freshness',return_value={'expected_date':'2026-11-13'});self.fresh_patch.start();self.addCleanup(self.fresh_patch.stop)
  self.store=AtomicStore();p,c=sample();self.report=d.build(p,c,c[-1],3);self.report['target']='2026-11-13';self.store.put_json(d.LATEST,self.report);app=FastAPI();app.include_router(d.routes(lambda:self.store,lambda request:request.headers.get('x-user','alice'),lambda request:None));self.client=TestClient(app)
 def test_pagination_and_matrix(self):
  r=self.client.get('/api/discovery?limit=1&current_only=false').json();self.assertEqual(r['total'],3);self.assertEqual(len(r['rows']),1);self.assertEqual(len(self.client.get('/api/discovery/matrix').json()['cells']),3)
 def test_saved_idempotent_and_account_isolation(self):
  pair=self.report['pairs'][0]['id'];body=dict(pair_id=pair,window=250)
  for _ in range(2):self.assertEqual(self.client.post('/api/discovery/watch',json=body).status_code,200)
  self.assertEqual(len(self.client.get('/api/discovery/watch').json()['items']),1);self.assertEqual(self.client.get('/api/discovery/watch',headers={'x-user':'bob'}).json()['items'],[])
  identity=pair+'_250';self.assertEqual(self.client.post('/api/discovery/watch/'+identity+'/action',headers={'x-user':'bob'},json={'action':'remove'}).status_code,404)
  self.client.post('/api/discovery/watch/'+identity+'/action',json={'action':'remove'});self.assertEqual(self.client.get('/api/discovery/watch').json()['items'],[])
 def test_new_calendar_day_marks_entire_snapshot_old(self):
  from app import daily_sync
  with patch.object(d.core,'calendar_dates',return_value=['2025-11-07','2026-11-13']),patch.object(daily_sync,'freshness',return_value={'expected_date':'2026-11-16'}):
   r=self.client.get('/api/discovery').json();self.assertTrue(r['snapshot_stale'])

class DailyCarryTests(unittest.TestCase):
 def test_next_day_reads_only_current_year_and_keeps_prior_prices(self):
  store=AtomicStore();code='THS111111';old='2026-11-12';target='2026-11-13';row=[old,10,10,10,10,1]
  store.put_json(d.STATE,dict(target=old,profiles={code:dict(name='A',last_date=old,rows=[row])},complete=True))
  new=[target,11,11,11,11,1]
  with patch.object(d.industry_ths,'fetch_catalog',return_value={'items':[dict(code=code,name='A')]}),patch.object(d.industry_ths,'load_year',return_value={'rows':[new]}) as load:
   result=d.sync_tick(store,target,[old,target]);self.assertTrue(result['complete']);self.assertEqual(load.call_count,1);self.assertEqual(load.call_args.args[2],2026);self.assertEqual(store.get_json(d.STATE)['profiles'][code]['rows'],[row,new])
