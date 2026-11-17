import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
from app import core,market,server,backfill,history_source

class MarketTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=patch.object(core,'DATA',Path(self.temp.name));self.data.start();core.init()
    def tearDown(self):self.data.stop();self.temp.cleanup()
    def master(self):return market.ingest([{'code':'000001','name':'平安银行'},{'code':'920002','name':'万达轴承'}],'stock','stock_info_a_code_name',inventory_only=True)
    def test_exchange_mapping_including_bse_new_codes(self):
        for code,kind,result in [('920002','stock','BJ'),('830799','stock','BJ'),('688001','stock','SH'),('300750','stock','SZ'),('510300','etf','SH'),('159915','etf','SZ')]:self.assertEqual(market.exchange(code,kind),result)
        with self.assertRaises(ValueError):market.exchange('900001','stock')
    def test_quote_feed_extras_not_counted_as_active_stocks(self):
        self.master();market.ingest([{'代码':'000001','名称':'平安银行','最新价':11.8},{'代码':'000003','名称':'PT金田','最新价':None}],'stock','stock_zh_a_spot_em')
        self.assertEqual(set(core.universe()),{'000001.SZ','920002.BJ','510300.SH','159915.SZ','510500.SH','510880.SH'})
        self.assertEqual(core.instrument('000003.SZ')['active'],0)
        with core.db() as c:self.assertIsNotNone(c.execute("SELECT * FROM market_quotes WHERE symbol='000003.SZ'").fetchone())
    def test_partial_membership_preserves_previous_universe(self):
        self.master();result=market.ingest([{'code':'000001','name':'平安银行'}],'stock','stock_info_a_code_name',inventory_only=True)
        self.assertEqual(result['status'],'partial');self.assertEqual(core.instrument('920002.BJ')['active'],1)
    def test_snapshot_time_is_not_fabricated_and_missing_price_retained(self):
        self.master();market.ingest([{'代码':'000001','名称':'平安银行','最新价':None}],'stock','stock_zh_a_spot_em')
        with TestClient(server.app) as client:
            data=client.get('/api/universe?q=000001').json()['items'][0]
        self.assertIsNone(data['latest']['close']);self.assertIsNone(data['latest']['event_time']);self.assertEqual(data['latest']['time_semantics'],'fetch_time_only')
        self.assertEqual(data['freshness']['label'],'源未提供有效价格')
    def test_search_filters_and_pagination_are_server_side(self):
        self.master()
        with TestClient(server.app) as client:
            a=client.get('/api/universe?kind=stock&limit=1').json();b=client.get('/api/universe?kind=stock&limit=1&offset=1').json()
            self.assertEqual(a['total'],2);self.assertNotEqual(a['items'][0]['symbol'],b['items'][0]['symbol'])
            self.assertEqual(client.get('/api/universe?exchange=BJ').json()['items'][0]['symbol'],'920002.BJ')
            self.assertEqual(client.get('/api/universe',params={'q':"' OR 1=1 --"}).json()['total'],0)
            self.assertEqual(client.get('/api/universe?exchange=BAD').status_code,400)
    def test_queue_resume_preserves_completed_tasks(self):
        self.master();backfill.enqueue()
        with core.db() as c:c.execute("UPDATE history_tasks SET status='done' WHERE symbol='000001.SZ'")
        backfill.enqueue()
        with core.db() as c:
            self.assertEqual(c.execute("SELECT status FROM history_tasks WHERE symbol='000001.SZ'").fetchone()[0],'done')
            self.assertEqual(c.execute("SELECT count(*) FROM history_tasks WHERE symbol='000001.SZ'").fetchone()[0],1)
    def test_daily_adapter_rejects_wrong_security(self):
        response=Mock();response.json.return_value={'rc':0,'data':{'code':'000002','market':0,'klines':[]}}
        with patch.object(history_source.requests,'get',return_value=response):
            with self.assertRaisesRegex(ValueError,'mismatch'):history_source.fetch({'exchange':'SZ','code':'000001'},{'start_date':'20250101','end_date':'20261109'})
    def test_daily_adapter_fields_and_timeout(self):
        response=Mock();response.json.return_value={'rc':0,'data':{'code':'000001','market':0,'klines':['2026-11-09,10,11,12,9,100,1100,3,1,0.1,2']}}
        with patch.object(history_source.requests,'get',return_value=response) as get:
            rows=history_source.fetch({'exchange':'SZ','code':'000001'},{'start_date':'20250101','end_date':'20261109'})
        self.assertEqual(rows[0]['收盘'],'11');self.assertEqual(get.call_args.kwargs['timeout'],(5,20))
    def test_coverage_does_not_equate_inventory_with_history(self):
        self.master();data=market.coverage()
        self.assertGreater(data['total'],0);self.assertEqual(data['quote_symbols'],0);self.assertEqual(data['history'],[])
    def test_direct_snapshot_adapter_pages_and_maps_fields(self):
        from app import spot_source
        def page(n):
            items=[{'f12':f'{510000+i}','f14':f'ETF{i}','f2':'-' if i==3 else 1.5,'f3':0.4,'f5':10,'f6':15,'f124':1762740000} for i in range((n-1)*100,min(n*100,150))]
            return Mock(**{'json.return_value':{'rc':0,'data':{'total':150,'diff':items}}})
        session=Mock();session.get.side_effect=lambda url,params,timeout:page(int(params['pn']))
        with patch.object(spot_source.requests,'Session') as S,patch.object(spot_source.time,'sleep'):
            S.return_value.__enter__.return_value=session
            rows=spot_source.fetch('fund_etf_spot_em')
        self.assertEqual(len(rows),150);self.assertIsNone(rows[3]['最新价']);self.assertEqual(rows[0]['更新时间'],'2025-11-10T02:00:00.000Z')
        self.assertEqual(market.ingest(rows,'etf','fund_etf_spot_em',adapter=spot_source.VERSION)['status'],'ok')
        with core.db() as c:
            batch=c.execute("SELECT parameters FROM batches WHERE endpoint='fund_etf_spot_em'").fetchone()[0]
            quote=json.loads(c.execute("SELECT payload FROM market_quotes WHERE symbol='510000.SH'").fetchone()[0])
        self.assertEqual(json.loads(batch),{'adapter':'direct-clist-v1'});self.assertEqual(quote['time_semantics'],'source_update')
    def test_direct_snapshot_incomplete_response_rejected(self):
        from app import spot_source
        session=Mock();session.get.return_value=Mock(**{'json.return_value':{'rc':0,'data':{'total':100,'diff':[{'f12':'510300','f14':'x','f2':1}]}}})
        with patch.object(spot_source.requests,'Session') as S,patch.object(spot_source.time,'sleep'):
            S.return_value.__enter__.return_value=session
            with self.assertRaisesRegex(ValueError,'Incomplete'):spot_source.fetch('fund_etf_spot_em')
    def test_direct_snapshot_failure_falls_back_to_sdk(self):
        with patch.object(market.spot_source,'fetch',side_effect=RuntimeError('blocked')),patch.object(market,'fetch_sdk',return_value=[{'代码':'000001'}]) as sdk:
            rows,adapter=market.fetch('stock_zh_a_spot_em')
        self.assertEqual(adapter,'akshare-sdk');self.assertEqual(rows,[{'代码':'000001'}]);sdk.assert_called_once()
    def test_source_block_stops_without_sdk_fallback(self):
        blocked=market.spot_source.SourceBlocked('reset')
        with patch.object(market.spot_source,'fetch',side_effect=blocked) as direct,patch.object(market,'fetch_sdk') as sdk:
            results=market.refresh()
        self.assertEqual([r['status'] for r in results],['error','error']);direct.assert_called_once();sdk.assert_not_called()
    def test_direct_snapshot_connection_reset_is_not_retried(self):
        from app import spot_source
        session=Mock();session.get.side_effect=spot_source.requests.ConnectionError('reset')
        with patch.object(spot_source.requests,'Session') as S,patch.object(spot_source.time,'sleep'):
            S.return_value.__enter__.return_value=session
            with self.assertRaises(spot_source.SourceBlocked):spot_source.fetch('stock_zh_a_spot_em')
        self.assertEqual(session.get.call_count,1)
    def test_backfill_requeue_is_incremental_after_clean_full_fetch(self):
        from datetime import datetime
        fetched='2026-11-10T09:00:00+00:00'
        calls=[]
        def fake(item,params):
            calls.append(params['start_date'])
            return [{'日期':'2026-11-09','开盘':4.1,'最高':4.3,'最低':4.0,'收盘':4.2,'成交量':1,'成交额':1}]
        with patch.object(core,'now',return_value=datetime.fromisoformat(fetched)),patch.object(history_source,'fetch',side_effect=fake),patch.object(backfill.time,'sleep'):
            with core.db() as c:c.execute("UPDATE instruments SET active=CASE WHEN symbol='510300.SH' THEN 1 ELSE 0 END")
            backfill.enqueue();backfill.run(workers=1)
            with core.db() as c:c.execute("UPDATE history_tasks SET status='pending'")
            backfill.run(workers=1)
        self.assertEqual(calls,['20211110','20261026'])
        data=market.coverage();self.assertEqual(data['history'][0]['symbols'],1)

if __name__=='__main__':unittest.main()
