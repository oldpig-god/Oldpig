import unittest,json,copy
from unittest.mock import patch,Mock
import requests
from app import industry_ths as t,sector_comparison as s
from test_data_health import AtomicStore

CODE='THS881105';DAY='2026-11-13'
def response(rows='20261113,10,12,9,11,100,200'):
 return 'quotebridge_v4_line_bk_881105_01_2026('+json.dumps({'data':rows})+')'
class THSTests(unittest.TestCase):
 def test_catalog_complete_block_and_duplicates(self):
  html='<div class="cate_inner"><div><a href="/thshy/detail/code/881105/">煤炭开采加工</a></div><a href="https://q.10jqka.com.cn/thshy/detail/code/881270/">元件</a></div>'
  with patch.object(t,'get_text',return_value=html):
   r=t.fetch_catalog();self.assertEqual(r['total'],2);self.assertEqual(r['items'][0]['code'],CODE);self.assertEqual(r['provider'],'ths')
  for text in [html.replace('881270','881105'),html[:-6],'<html>no catalog</html>']:
   with patch.object(t,'get_text',return_value=text),self.assertRaises(ValueError):t.fetch_catalog()
 def test_parse_identity_ohlc_mapping(self):
  self.assertEqual(t.parse_year(response(),CODE,2026),[[DAY,10.,11.,12.,9.,100.]])
  with self.assertRaises(ValueError):t.parse_year(response(),'THS881270',2026)
 def test_reject_malformed_duplicate_and_bad_ohlc(self):
  for raw in ['20261113,10,12,9,20,100,200','20251113,10,12,9,11,100,200','20261113,10,12,9,nan,100,200','20261113,10,12,9,11,100,200;20261113,10,12,9,11,100,200']:
   with self.assertRaises(ValueError):t.parse_year(response(raw),CODE,2026)
 def test_invalid_history_never_replaces_good_year(self):
  store=AtomicStore();key=t.PREFIX+'years/'+CODE+'/2026.json.gz';old={'rows':[['2026-11-12',10,11,12,9,100]],'fetched_at':'old'};store.put_json(key,old)
  with patch.object(t,'get_text',return_value=response('20261113,10,12,9,-1,100,200')),self.assertRaises(ValueError):t.load_year(store,CODE,2026,DAY,['2026-11-12',DAY],s.validate_rows)
  self.assertEqual(store.get_json(key),old)
 def test_closed_year_cache_reused(self):
  store=AtomicStore();key=t.PREFIX+'years/'+CODE+'/2025.json.gz';store.put_json(key,{'rows':[['2025-11-13',1,1,1,1,100]],'closed_year':True})
  with patch.object(t,'get_text') as get:t.load_year(store,CODE,2025,DAY,[DAY],s.validate_rows);get.assert_not_called()
 def test_missing_year_is_explicit_not_network_failure(self):
  store=AtomicStore()
  with patch.object(t,'get_text',return_value=None):r=t.load_year(store,CODE,2026,DAY,[DAY],s.validate_rows)
  self.assertEqual(r['status'],'source_no_year');self.assertEqual(r['rows'],[])
 def test_same_date_cache_is_deduplicated(self):
  store=AtomicStore();value=dict(name='煤炭',start=DAY,end=DAY,rows=[[DAY,10,11,12,9,100]])
  store.put_json(t.PREFIX+'prices/'+DAY+'/'+CODE+'.json.gz',value)
  with patch.object(t,'get_text') as get:self.assertEqual(t.fetch_asset(CODE,'煤炭',store,DAY,DAY,[DAY],s.validate_rows),value);get.assert_not_called()
 def test_source_failure_preserves_last_good_with_explicit_stale(self):
  store=AtomicStore();start='2026-11-12';old={'rows':[[start,10,11,12,9,100]],'name':'煤炭','fetched_at':'old','source':t.SOURCE};key=t.PREFIX+'last-good/'+CODE+'.json.gz';store.put_json(key,old)
  with patch.object(t.core,'calendar_dates',return_value=[start,DAY]),patch.object(t,'load_year',side_effect=TimeoutError()):r=t.fetch_asset(CODE,'煤炭',store,start,DAY,[start,DAY],s.validate_rows)
  self.assertIn('refresh_error',r);self.assertEqual(store.get_json(key),old);self.assertEqual(r['rows'][-1][0],start)
 def test_no_stale_cache_does_not_invent_results(self):
  with patch.object(t.core,'calendar_dates',return_value=[DAY]),patch.object(t,'load_year',side_effect=TimeoutError()),self.assertRaises(TimeoutError):t.fetch_asset(CODE,'煤炭',AtomicStore(),DAY,DAY,[DAY],s.validate_rows)
 def test_network_retry_bounded(self):
  good=Mock(status_code=200,content=b'good',text='good')
  with patch.object(t,'headers',return_value={}),patch.object(t.requests,'get',side_effect=[requests.Timeout(),good]) as get,patch.object(t.time,'sleep'):
   self.assertEqual(t.get_text('https://example.test'),'good');self.assertEqual(get.call_count,2)
 def test_no_taxonomy_mixing(self):
  from pydantic import ValidationError
  with self.assertRaises(ValidationError):s.Selection(left='BK0437',right='THS881270')

 def test_intraday_same_date_row_requires_postclose_refetch(self):
  store=AtomicStore();key=t.PREFIX+'years/'+CODE+'/2026.json.gz'
  store.put_json(key,{'rows':[[DAY,10,10,12,9,100]],'expected_end':'2026-11-12','closed_year':False})
  with patch.object(t,'get_text',return_value=response()) as get:
   r=t.load_year(store,CODE,2026,DAY,[DAY],s.validate_rows);get.assert_called_once();self.assertEqual(r['rows'][-1][2],11);self.assertEqual(r['expected_end'],DAY)

 def test_transient_gateway_retry_is_bounded(self):
  good=Mock(status_code=200,content=b'good',text='good')
  for status in (502,503,504):
   bad=Mock(status_code=status);bad.raise_for_status.side_effect=requests.HTTPError()
   with patch.object(t,'headers',return_value={}),patch.object(t.requests,'get',side_effect=[bad,good]) as get,patch.object(t.time,'sleep'):
    self.assertEqual(t.get_text('https://example.test'),'good');self.assertEqual(get.call_count,2)
   with patch.object(t,'headers',return_value={}),patch.object(t.requests,'get',return_value=bad) as get,patch.object(t.time,'sleep'):
    with self.assertRaises(requests.HTTPError):t.get_text('https://example.test')
    self.assertEqual(get.call_count,2)
