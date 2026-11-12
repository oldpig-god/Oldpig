import copy
from datetime import datetime
import unittest
from unittest.mock import patch
from app import daily_sync as d

class Store:
 def __init__(self):self.values={}
 def get_json(self,key,optional=False):return copy.deepcopy(self.values.get(key))
 def put_json(self,key,value):self.values[key]=copy.deepcopy(value)

class DailySyncTests(unittest.TestCase):
 days=['2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']
 def at(self,value):return datetime.fromisoformat(value+'+08:00')
 def test_close_cutoff_and_weekend(self):
  self.assertEqual(d.freshness('2026-11-11',self.days,self.at('2026-11-12T17:29'))['state'],'current')
  f=d.freshness('2026-11-10',self.days,self.at('2026-11-12T17:30'));self.assertEqual(f['lag_sessions'],2)
  self.assertEqual(d.freshness('2026-11-13',self.days,self.at('2026-11-14T18:00'))['state'],'current')
 def test_calendar_unknown_not_fresh(self):self.assertEqual(d.freshness('2026-11-18',self.days,self.at('2026-11-18T18:00'))['state'],'unknown')
 def test_merge_cannot_regress_newer_manual_data(self):
  a={'records':[dict(event_time='2026-11-12',fetched_at='2026-11-12T19:00',close=5)]}
  b={'records':[dict(event_time='2026-11-12',fetched_at='2026-11-12T18:00',close=4),dict(event_time='2026-11-11',close=3)]}
  r=d.merge_history(a,b);self.assertEqual([x['close'] for x in r['records']],[3,5])
 def test_resume_backoff_and_next_day_recovery(self):
  store=Store();at=self.at('2026-11-12T18:00');d.CATALOG={'entries':{'600031.SH/1d':{'end':'2026-11-10'}}}
  with patch.object(d.core,'calendar_dates',return_value=self.days),patch.object(d.core,'universe',return_value={'600031.SH':{}}),patch.object(d.tencent_source,'refresh_series',side_effect=TimeoutError) as fetch:
   r=d.run(store,at=at);self.assertEqual(r['counts']['retry'],1);d.run(store,at=at);self.assertEqual(fetch.call_count,1)
   for t in ['2026-11-12T18:16','2026-11-12T19:17','2026-11-12T22:18']:r=d.run(store,at=self.at(t))
   self.assertEqual(r['counts']['exhausted'],1)
   r=d.run(store,at=self.at('2026-11-13T18:00'));self.assertEqual(r['counts']['retry'],1)
 def test_gap_not_silently_success_and_success_idempotent(self):
  store=Store();d.CATALOG={'entries':{'600031.SH/1d':{'end':'2026-11-10'}}}
  with patch.object(d.core,'calendar_dates',return_value=self.days),patch.object(d.core,'universe',return_value={'600031.SH':{}}),patch.object(d.tencent_source,'refresh_series',return_value={'records':[{'event_time':'2026-11-12'}]}) as fetch:
   r=d.run(store,at=self.at('2026-11-12T18:00'));self.assertEqual(r['counts']['retry'],1);self.assertEqual(store.values[d.STATE]['jobs']['600031.SH']['missing_dates'],['2026-11-11'])
   fetch.return_value={'records':[{'event_time':'2026-11-11'},{'event_time':'2026-11-12'}]}
   r=d.run(store,at=self.at('2026-11-12T18:16'));self.assertTrue(r['complete']);d.run(store,at=self.at('2026-11-12T18:20'));self.assertEqual(fetch.call_count,2)
   self.assertEqual(fetch.call_args.kwargs['end'],'2026-11-12')
 def test_budget_persists_progress(self):
  store=Store();clock=iter([0,100])
  with patch.object(d.core,'calendar_dates',return_value=self.days),patch.object(d.core,'universe',return_value={'600031.SH':{}}):
   r=d.run(store,at=self.at('2026-11-12T18:00'),clock=lambda:next(clock),budget_seconds=1);self.assertEqual(r['counts']['pending'],1)

class InvocationGuardTests(unittest.TestCase):
 def test_duplicate_and_late_delivery_never_run(self):
  from unittest.mock import Mock
  store=Mock();store.get_json.return_value={'enabled':True};store.claim.return_value=False
  at=datetime.fromisoformat('2026-11-12T18:00:10+08:00')
  with patch('app.cloud_store.CloudStore',return_value=store),patch.object(d.core,'now',return_value=at),patch.object(d,'run') as run:
   self.assertEqual(d.main({},None)['state'],'claim_not_acquired');run.assert_not_called()
  with patch('app.cloud_store.CloudStore',return_value=store),patch.object(d.core,'now',return_value=at.replace(second=50)),patch.object(d,'run') as run:
   self.assertEqual(d.main({},None)['state'],'deferred_late_delivery');run.assert_not_called()
 def test_outside_window_does_not_access_storage(self):
  with patch('app.cloud_store.CloudStore') as store,patch.object(d.core,'now',return_value=datetime.fromisoformat('2026-11-12T12:00:00+08:00')):
   self.assertEqual(d.main({},None)['state'],'outside_window');store.assert_not_called()
