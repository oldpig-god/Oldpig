import copy, unittest, threading
from datetime import datetime
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from app import data_health as h,health_service as s,health_status_source as source
from test_daily_sync import Store

class AtomicStore(Store):
 def __init__(self):super().__init__();self.lock=threading.Lock()
 def create_json(self,key,value):
  with self.lock:
   if key in self.values:return False
   self.put_json(key,value);return True
 def list_keys(self,prefix):return [k for k in self.values if k.startswith(prefix)]

class HealthTests(unittest.TestCase):
 days=['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']
 at=datetime.fromisoformat('2026-11-13T20:00:00+08:00')
 def test_error_taxonomy_retains_unknown_and_redacts(self):
  for error,category in [(TimeoutError('timed out'),'timeout'),(RuntimeError('HTTP 429'),'rate_limit'),(ValueError('Empty Tencent history'),'empty'),(ValueError('schema mismatch'),'format'),(ValueError('Validation rejected'),'validation'),(RuntimeError('Cloud storage request failed'),'storage'),(RuntimeError('weird original error'),'unknown')]:
   self.assertEqual(h.error_info(error)['error_category'],category)
  error=h.error_info(RuntimeError('odd https://x/a?secret=abc password=hello token=abc'))
  self.assertIn('odd',error['error_message']);self.assertNotIn('hello',error['error_message']);self.assertNotIn('https://',error['error_message'])
 def test_normal_absences_excluded_and_unverified_never_inferred(self):
  index={k:dict(name=k,last_date='2026-11-02',missing_dates=[]) for k in ['suspended','delisted','unknown','new']}
  state=dict(target='2026-11-13',jobs={k:dict(status='exhausted') for k in index})
  evidence={k:dict(source='exchange',verified_at=self.at.isoformat(),**v) for k,v in {'suspended':dict(suspensions=[dict(start='2026-11-03',end='2026-11-13')]),'delisted':dict(delisted_at='2026-11-05'),'new':dict(listed_at='2026-11-16')}.items()}
  report=h.assess(index,state,self.days,self.at,evidence=evidence)
  self.assertEqual(report['counts']['skipped'],3);self.assertEqual(report['counts']['failed'],1);self.assertEqual(report['counts']['stale'],1)
  self.assertEqual(report['counts']['expected'],1)
 def test_internal_gap_grouping_repair_and_no_weekends(self):
  present=[self.days[0],self.days[3],self.days[4]]
  gaps=h.gaps(present,self.days);self.assertEqual(gaps['missing'],self.days[1:3]);self.assertEqual(gaps['ranges'][0]['count'],2)
  self.assertEqual(h.gaps(present+self.days[1:3],self.days)['missing'],[])
  ev=dict(source='exchange',verified_at='now',suspensions=[dict(start=self.days[1],end=self.days[2])])
  self.assertEqual(h.gaps(present,self.days,ev)['missing'],[])
  self.assertEqual(h.gaps([self.days[4],self.days[5]],self.days)['missing'],[])
 def test_stalled_and_calendar_expiry_and_holiday(self):
  state=dict(target='2026-11-13',jobs={},invocation=dict(status='running',started_at='2026-11-13T17:00:00+08:00',heartbeat='2026-11-13T17:10:00+08:00'))
  report=h.assess({},state,self.days,self.at)
  keys={r['key'] for r in report['rules']};self.assertIn('stalled',keys);self.assertIn('calendar_expiry',keys)
  report=h.assess({},state,['2026-10-01']+self.days,self.at)
  self.assertIn('2026-10-01',report['calendar']['invalid_days'])
 def test_pending_launch_and_grace(self):
  index={'a':dict(last_date='2026-11-12',missing_dates=[])}
  report=h.assess(index,{},self.days,self.at)
  self.assertIn('not_started',[r['key'] for r in report['rules']])
  intraday=h.assess(index,dict(target='2026-11-12',jobs={'a':dict(status='current')}),self.days,self.at.replace(hour=12))
  self.assertNotIn('not_started',[r['key'] for r in intraday['rules']])
 def test_atomic_parallel_requests(self):
  store=AtomicStore()
  with ThreadPoolExecutor(max_workers=8) as pool:
   results=list(pool.map(lambda _:s.request_repair(store,['600031.SH'],'2026-11-02','2026-11-13','owner','2026-11-13'),range(20)))
  self.assertEqual(len(store.list_keys(s.QUEUE)),1);self.assertEqual(len({r['id'] for r in results}),1)
  self.assertEqual(sum(not r['duplicate'] for r in results),1)
 def test_running_symbol_rejected(self):
  from fastapi import HTTPException
  store=AtomicStore();store.put_json('system/daily-sync/state.json.gz',dict(repair_running=['600031.SH']))
  with self.assertRaises(HTTPException) as result:s.request_repair(store,['600031.SH'],'2026-11-02','2026-11-13','owner','2026-11-13')
  self.assertEqual(result.exception.status_code,409)
 def test_repair_closes_gap_and_replay_no_duplicate_fetch(self):
  store=AtomicStore();symbol='600031.SH';present=[self.days[0],self.days[2]]
  base=dict(records=[dict(event_time=d,close=1) for d in present]);store.put_json('base',{'600031.SH/1d':base})
  catalog=dict(entries={'600031.SH/1d':dict(chunk='base')});index={symbol:dict(last_date=present[-1],missing_dates=[self.days[1]])}
  s.request_repair(store,[symbol],self.days[1],self.days[1],'owner','2026-11-13')
  result=dict(records=base['records']+[dict(event_time=self.days[1],close=1)])
  with patch('app.tencent_source.refresh_series',return_value=result) as fetch,patch.object(s.time,'sleep'):
   s.process_repairs(store,{},index,catalog,self.days)
   s.process_repairs(store,{},index,catalog,self.days)
   self.assertEqual(fetch.call_count,1)
  self.assertEqual(index[symbol]['missing_dates'],[])
 def test_failed_repair_preserves_old_data(self):
  store=AtomicStore();old={'records':[dict(event_time='2026-11-02',close=2)]};key='scheduled/600031.SH/1d.json.gz';store.put_json(key,old)
  s.request_repair(store,['600031.SH'],'2026-11-03','2026-11-03','owner','2026-11-13')
  with patch('app.tencent_source.refresh_series',side_effect=ValueError('Validation rejected')),patch.object(s.time,'sleep'):
   s.process_repairs(store,{}, {},{},self.days)
  self.assertEqual(store.get_json(key),old)
  results=[store.get_json(k) for k in store.list_keys(s.RESULTS)];self.assertEqual(results[0]['items']['600031.SH']['error_category'],'validation')
 def test_suspension_capped_at_evidence_day_and_intraday_ignored(self):
  rows=[dict(SECUCODE='000635.SZ',SUSPEND_START_DATE='2026-11-13',SUSPEND_END_TIME='2026-11-19',SUSPEND_EXPIRE='连续停牌'),dict(SECUCODE='600031.SH',SUSPEND_START_DATE='2026-11-13',SUSPEND_EXPIRE='盘中停牌')]
  result=source.suspension_evidence(rows,'2026-11-13');self.assertNotIn('600031.SH',result)
  self.assertEqual(result['000635.SZ']['suspensions'][0]['end'],'2026-11-13')
 def test_all_stats_drilldown(self):
  report=h.assess({'a':dict(last_date='2026-11-02',missing_dates=['2026-11-03'])},dict(target='2026-11-13',jobs={'a':dict(status='exhausted')}),self.days,self.at)
  for key in ('expected','success','failed','skipped','stale','missing','gaps','normal_gaps','unverified'):
   self.assertEqual(report['counts'][key],len(h.select(report['rows'],key)))
 def test_thresholds_validation(self):
  for p in [dict(stale_warn_sessions=6,stale_error_sessions=2),dict(repair_concurrency=100),dict(completion_min=2),dict(page_size=1.5)]:
   with self.assertRaises(ValueError):h.policy(p)

if __name__=='__main__':unittest.main()

class IntegrationFaultTests(unittest.TestCase):
 def test_bad_provider_batch_never_replaces_good_overlay(self):
  import json
  from pathlib import Path
  from app import tencent_source as t
  store=AtomicStore();old=dict(records=[dict(event_time='2026-11-02',close=5)])
  store.put_json('scheduled/600031.SH/1d.json.gz',old)
  def fetch(spec,directory,start,end):
   (Path(directory)/'raw.json').write_text(json.dumps({'responses':[]}));return {'file':'raw.json'}
  # The second candle is impossible: low exceeds its open and close.
  rows=[['2026-11-03','5','6','7','4','10'],['2026-11-04','5','6','7','8','10']]
  with patch.object(t,'fetch_one',side_effect=fetch),patch.object(t,'rows_from_evidence',return_value=(rows,'三一重工')):
   with self.assertRaises(ValueError):t.refresh_series('600031.SH','1d',old,store,start='2026-11-03',end='2026-11-04',output_prefix='scheduled')
  self.assertEqual(store.get_json('scheduled/600031.SH/1d.json.gz'),old)
  self.assertEqual(len(store.values),1)
 def test_failed_source_visible_and_success_streak_recovers(self):
  from app import daily_sync as d
  days=HealthTests.days;at=HealthTests.at;store=AtomicStore()
  d.CATALOG={'entries':{'600031.SH/1d':{'end':'2026-11-12'}}}
  with patch.object(d.core,'calendar_dates',return_value=days),patch.object(d.core,'universe',return_value={'600031.SH':{'name':'三一重工'}}),patch.object(d.tencent_source,'refresh_series',side_effect=TimeoutError('source timed out')):
   d.run(store,at=at)
  snap=store.get_json(s.SNAPSHOT);self.assertEqual(snap['counts']['failed'],1);self.assertEqual(snap['rows'][0]['error_category'],'timeout');self.assertEqual(snap['sources']['Tencent']['timeout'],1)
 def test_frozen_snapshot_does_not_hide_later_stall(self):
  store=AtomicStore();s.invalidate();at=HealthTests.at
  state=dict(target='2026-11-13',jobs={},invocation=dict(status='running',started_at='2026-11-13T19:59:00+08:00',heartbeat='2026-11-13T19:59:30+08:00'))
  with patch.object(s.core,'calendar_dates',return_value=HealthTests.days):s.publish(store,state,{},at)
  store.put_json('system/daily-sync/status.json.gz',dict(invocation=state['invocation']))
  with patch.object(s.core,'now',return_value=at.replace(hour=21)):
   report=s.read_snapshot(store)
  self.assertIn('live_stalled',[r['key'] for r in report['rules']]);s.invalidate()

class InterruptedWorkerTests(unittest.TestCase):
 def test_platform_timeout_becomes_visible_failure(self):
  from app import daily_sync as d
  store=Store();state=dict(target='2026-11-13',jobs={'600031.SH':dict(status='running',attempts=4)},invocation=dict(active_symbols=['600031.SH'],status='running'))
  store.put_json(d.STATE,state)
  with patch.object(d.core,'calendar_dates',return_value=HealthTests.days),patch.object(d.core,'universe',return_value={'600031.SH':{}}),patch.object(d.tencent_source,'refresh_series') as fetch:
   d.run(store,at=HealthTests.at);fetch.assert_not_called()
  job=store.get_json(d.STATE)['jobs']['600031.SH'];self.assertEqual(job['status'],'exhausted');self.assertEqual(job['error_category'],'timeout')

class HealthRouteTests(unittest.TestCase):
 def setUp(self):
  from fastapi import FastAPI,HTTPException
  from fastapi.testclient import TestClient
  self.store=AtomicStore();s.invalidate()
  idx={f'{i:06d}.SZ':dict(name=f'证券{i}',last_date='2026-11-02',missing_dates=[]) for i in range(201)}
  state=dict(target='2026-11-13',jobs={k:dict(status='exhausted',error_category='timeout') for k in idx})
  with patch.object(s.core,'calendar_dates',return_value=HealthTests.days):s.publish(self.store,state,idx,HealthTests.at)
  app=FastAPI()
  def check(req):
   if req.headers.get('x-workbench-action')!='local-refresh':raise HTTPException(403,'denied')
  app.include_router(s.routes(lambda:self.store,lambda req:'owner',check));self.client=TestClient(app)
 def tearDown(self):s.invalidate()
 def test_backend_pagination_and_complete_filter_repair(self):
  r=self.client.get('/api/data-health/securities?view=failed&size=50&page=2');self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['items']),50);self.assertEqual(r.json()['total'],201)
  r=self.client.post('/api/data-health/repair',json={'view':'failed','reason':'timeout','start':'2026-11-02','end':'2026-11-13'},headers={'x-workbench-action':'local-refresh'})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['request']['symbols']),201)
  r2=self.client.post('/api/data-health/repair',json={'view':'failed','reason':'timeout','start':'2026-11-02','end':'2026-11-13'},headers={'x-workbench-action':'local-refresh'});self.assertTrue(r2.json()['duplicate'])
 def test_write_guard_and_invalid_dates(self):
  self.assertEqual(self.client.post('/api/data-health/repair',json={'symbols':['000000.SZ']}).status_code,403)
  r=self.client.post('/api/data-health/repair',json={'symbols':['000000.SZ'],'start':'bad'},headers={'x-workbench-action':'local-refresh'});self.assertEqual(r.status_code,400)
 def test_overview_does_not_send_all_rows(self):
  r=self.client.get('/api/data-health');self.assertEqual(r.status_code,200);self.assertNotIn('rows',r.json());self.assertNotIn('calendar_days',r.json())
 def test_explicit_failed_retry_is_deduplicated_child(self):
  original=s.request_repair(self.store,['000000.SZ'],'2026-11-02','2026-11-13','owner','2026-11-13')
  self.store.put_json(s.RESULTS+original['id']+'.json.gz',dict(status='complete',items={'000000.SZ':dict(status='failed',error_category='timeout')}))
  path='/api/data-health/repairs/'+original['id']+'/retry';headers={'x-workbench-action':'local-refresh'}
  first=self.client.post(path,headers=headers);second=self.client.post(path,headers=headers)
  self.assertEqual(first.status_code,200,first.text);self.assertNotEqual(first.json()['id'],original['id']);self.assertEqual(first.json()['id'],second.json()['id']);self.assertTrue(second.json()['duplicate'])
 def test_overview_never_reads_full_market_detail_or_storage_inventory(self):
  s.invalidate()
  with patch.object(self.store,'get_json',wraps=self.store.get_json) as read:
   response=self.client.get('/api/data-health')
  self.assertEqual(response.status_code,200)
  keys=[c.args[0] for c in read.call_args_list]
  self.assertIn(s.OVERVIEW,keys);self.assertNotIn(s.SNAPSHOT,keys);self.assertNotIn(s.INDEX,keys);self.assertNotIn('system/daily-sync/state.json.gz',keys)
  self.assertLessEqual(len(response.json()['initial_failures']),50)
