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

if __name__=='__main__':unittest.main()
