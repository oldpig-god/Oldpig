import unittest
from unittest.mock import patch
from app import security_research
from test_strategy import fixture

class SecurityResearchTests(unittest.TestCase):
    def payload(self,n=100,symbol='688001.SH',name='华兴源创',kind='stock'):
        b=fixture(n);dates=b['calendar'];rows=[dict(bar,event_time=bar['date']+'T15:00:00+08:00',volume_raw=bar['volume'],source='test') for bar in b['assets']['600031.SH']['bars']]
        return dict(instrument=dict(symbol=symbol,name=name,kind=kind),records=rows,source='test',quote=None),dates
    def test_all_boards_and_st_can_analyze(self):
        for symbol,name in [('600031.SH','三一重工'),('300750.SZ','宁德时代'),('688001.SH','华兴源创'),('920002.BJ','万达轴承'),('600079.SH','ST人福')]:
            p,d=self.payload(symbol=symbol,name=name);r=security_research.analyze(p,d)
            self.assertEqual(r['status'],'ready');self.assertIsNotNone(r['summary']['rsi14'])
            if name.startswith('ST'):self.assertFalse(r['profile']['backtest_allowed'])
    def test_etfs_observation_separate_from_backtest(self):
        p,d=self.payload(symbol='159915.SZ',name='创业板ETF',kind='etf');r=security_research.analyze(p,d)
        self.assertEqual(r['status'],'ready');self.assertFalse(r['profile']['backtest_allowed']);self.assertEqual(r['profile']['board'],'etf')
    def test_short_history_keeps_indicators_null(self):
        p,d=self.payload(8);r=security_research.analyze(p,d)
        self.assertEqual(r['status'],'limited_history');self.assertIsNone(r['summary']['rsi14']);self.assertIsNone(r['summary']['ma60'])
    def test_missing_and_zero_volume_reset_indicators(self):
        p,d=self.payload();p['records'].pop(75);p['records'][-1]['volume_raw']=0;r=security_research.analyze(p,d)
        self.assertEqual(r['quality']['missing_dates'],[d[75]]);self.assertEqual(r['quality']['zero_volume_dates'],[d[-1]])
        self.assertIsNone(r['series'][76]['ma20']);self.assertIsNone(r['series'][-1]['close'])
    def test_invalid_rows_excluded_and_conflicts_rejected(self):
        p,d=self.payload();p['records'][40]['high']=0;r=security_research.analyze(p,d)
        self.assertEqual(len(r['quality']['issues']),1)
        p,d=self.payload();p['records'].append(dict(p['records'][0],close=p['records'][0]['close']+.01))
        with self.assertRaises(ValueError):security_research.analyze(p,d)
    def test_no_data_is_an_explicit_supported_state(self):
        p,d=self.payload();p['records']=[];r=security_research.analyze(p,d)
        self.assertEqual(r['status'],'no_history');self.assertEqual(r['series'],[])

if __name__=='__main__':unittest.main()
