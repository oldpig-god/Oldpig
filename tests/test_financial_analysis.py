import unittest
from datetime import datetime,date,timezone
from app.financial_analysis import normalize,analysis,report

def row(day,revenue=100,profit=10,**kwargs):
    return dict(SECUCODE='600031.SH',REPORT_DATE=day,NOTICE_DATE=day,CURRENCY='CNY',TOTALOPERATEREVE=revenue,PARENTNETPROFIT=profit,KCFJCXSYJLR=profit-1,**kwargs)
class Store:
    def __init__(self):self.data={}
    def get_json(self,k,optional=False):return self.data.get(k)
    def put_json(self,k,v):self.data[k]=v
class FinancialTests(unittest.TestCase):
    def test_ttm(self):
        rows=normalize([row('2025-09-30',80,8),row('2025-12-31',100,10),row('2026-09-30',90,9)],'600031.SH',date(2026,11,14))
        self.assertEqual(analysis(rows,'通用')['ttm']['revenue'],110)
        self.assertEqual(analysis(rows,'通用')['ttm']['profit'],11)
        self.assertIsNone(analysis(rows[1:],'通用')['ttm']['revenue'])
    def test_null_zero_and_negative_profit(self):
        rows=normalize([row('2026-09-30',0,-10,XSMLL=None)],'600031.SH',date(2026,11,14))
        self.assertEqual(rows[0]['revenue'],0);self.assertEqual(rows[0]['profit'],-10);self.assertIsNone(rows[0]['gross_margin'])
    def test_identity_invalid_and_conflict(self):
        for raw in [[dict(row('2026-09-30'),SECUCODE='000001.SZ')],[row('2026-09-30',float('inf'))],[row('2026-09-30',-1)],[row('2026-09-30'),row('2026-09-30',90)]]:
            with self.assertRaises(ValueError):normalize(raw,'600031.SH',date(2026,11,14))
    def test_future_disclosures_and_five_years(self):
        rows=normalize([row('2020-09-30'),row('2025-12-31'),dict(row('2026-09-30'),NOTICE_DATE='2026-12-01')],'600031.SH',date(2026,11,14))
        self.assertEqual([r['date'] for r in rows],['2025-12-31'])
    def test_cache_and_failed_refresh_preserves(self):
        store=Store();i=dict(symbol='600031.SH',kind='stock',name='三一重工');calls=[]
        def fetch(_):calls.append(1);return [row('2026-09-30')]
        a=report(i,store,fetch,datetime(2026,11,14,tzinfo=timezone.utc));b=report(i,store,fetch,datetime(2026,11,14,tzinfo=timezone.utc));self.assertEqual(a,b);self.assertEqual(len(calls),1)
        def bad(_):return [row('2026-09-30',-1)]
        c=report(i,store,bad,datetime(2026,11,15,tzinfo=timezone.utc));self.assertEqual(c['status'],'stale');self.assertEqual(next(iter(store.data.values())),a)
    def test_etf_and_financial_industry(self):
        self.assertEqual(report(dict(symbol='510300.SH',kind='etf'),Store())['status'],'unsupported')
        rows=normalize([row('2026-09-30')],'600031.SH',date(2026,11,14));self.assertTrue(analysis(rows,'银行')['financial_industry'])
