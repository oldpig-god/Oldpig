import copy
import unittest
from dataclasses import replace
from datetime import date,timedelta
from app.strategy import Config,simulate,signals,run,costs,random_test
from app.strategy_data import validate,digest,profile


def fixture(n=800,flat=False):
    days=[];day=date(2022,1,3)
    while len(days)<n:
        if day.weekday()<5:days.append(day.isoformat())
        day+=timedelta(days=1)
    bars=[]
    import math
    for i,d in enumerate(days):
        p=10 if flat else 15+2*math.sin(i/24)+i*.003
        bars.append(dict(date=d,open=p,close=p,high=p*1.02,low=p*.98,volume=10000,adjusted_close=p))
    asset=dict(name='测试',kind='stock',lot=100,tick=.01,limit_rate=.1,bars=bars)
    bundle=dict(calendar=days,assets={'600031.SH':asset,'510300.SH':dict(asset,kind='etf',tick=.001)},symbol='600031.SH',benchmark='510300.SH',source='synthetic test only',adjustment='identity',quality=[],captured_at='2026-01-01')
    bundle['data_hash']=digest(bundle)
    return bundle

class DataTests(unittest.TestCase):
    def test_calendar_duplicate_and_ohlc_rejected(self):
        b=fixture();b['calendar'][1]=b['calendar'][0]
        with self.assertRaises(ValueError):validate(b)
        b=fixture();b['assets']['600031.SH']['bars'][0]['low']=100
        with self.assertRaises(ValueError):validate(b)
    def test_missing_remains_explicit_and_resets_warmup(self):
        b=fixture();bars=b['assets']['600031.SH']['bars'];bars[70]=None
        s=signals(bars,20,60);self.assertIsNone(s[70]);self.assertIsNone(s[129]);self.assertIsNotNone(s[130])
    def test_reject_bad_hash(self):
        b=fixture();b['calendar'][0]='2001-01-01'
        with self.assertRaises(ValueError):run(b)
    def test_unsupported_board_and_st(self):
        for symbol,name in [('600001.SH','ST test'),('999999.SH','test'),('159915.SZ','创业板ETF')]:
            with self.assertRaises(ValueError):profile(symbol,name)

    def test_star_beijing_rule_profiles(self):
        star=profile('688001.SH','华兴源创');bj=profile('920002.BJ','万达轴承')
        self.assertEqual((star['min_buy'],star['lot'],star['limit_rate']),(200,1,.2))
        self.assertEqual((bj['min_buy'],bj['lot'],bj['limit_rate']),(100,1,.3))
        self.assertFalse(profile('000001.SZ','ST测试',allow_research=True)['backtest_allowed'])

class EngineTests(unittest.TestCase):
    def setUp(self):
        b=fixture(10,True);self.asset=b['assets']['600031.SH'];self.days=b['calendar'];self.cfg=Config(capital=10000,commission=.0003,minimum=5,transfer=0,slippage=0,historical_stamp=False)
    def simulate(self,target,**kwargs):return simulate(self.asset,self.days,target,self.cfg,**kwargs)
    def test_hand_calculated_round_lot_costs_next_day_t1(self):
        result=self.simulate([1,0]+[0]*8)
        buys,sells=[t for t in result['trades'] if t['status']=='filled']
        self.assertEqual((buys['signal_date'],buys['date'],buys['quantity']), (self.days[0],self.days[1],900))
        self.assertEqual(sells['date'],self.days[2]);self.assertEqual(sells['holding_days'],1)
        self.assertEqual(buys['fees']['total'],5);self.assertEqual(sells['fees']['total'],9.5)
        self.assertEqual(result['curve'][-1]['equity'],9985.5)
    def test_star_minimum_and_one_share_step(self):
        self.asset.update(profile('688001.SH','test'))
        r=self.simulate([1]+[0]*9)
        self.assertEqual(r['trades'][0]['quantity'],999)
        self.cfg=replace(self.cfg,capital=1999)
        self.assertEqual(self.simulate([1]*10)['trades'][0]['reason'],'insufficient_cash_for_lot')

    def test_limit_up_buy_and_limit_down_sell(self):
        self.asset['bars'][1].update(open=11,close=11,high=11,low=11,adjusted_close=11)
        self.assertEqual(self.simulate([1]*10)['trades'][0]['reason'],'limit_up')
        self.setUp();self.asset['bars'][2].update(open=9,close=9,high=9,low=9,adjusted_close=9)
        r=self.simulate([1,0]+[0]*8)
        self.assertEqual(r['trades'][1]['reason'],'limit_down');self.assertEqual(r['trades'][2]['date'],self.days[3])
    def test_suspension_and_missing_no_fill_marked(self):
        self.asset['bars'][1]['volume']=0
        self.assertEqual(self.simulate([1]*10)['trades'][0]['reason'],'suspended_zero_volume')
        self.setUp();self.asset['bars'][2]=None
        r=self.simulate([1]*10);self.assertEqual(r['curve'][2]['valuation'],'stale_mark_missing_bar')
        self.assertEqual(r['curve'][2]['equity'],r['curve'][1]['equity'])
    def test_slippage_not_fabricated_outside_range(self):
        self.cfg=replace(self.cfg,slippage=.01);self.asset['bars'][1]['high']=10
        self.assertEqual(self.simulate([1]*10)['trades'][0]['reason'],'slippage_outside_observed_range')
    def test_stamp_date_and_etf_exemption(self):
        cfg=Config();self.assertEqual(costs(10000,'sell','2023-08-25',self.asset,cfg)['stamp'],10)
        self.assertEqual(costs(10000,'sell','2023-08-28',self.asset,cfg)['stamp'],5)
        self.assertEqual(costs(10000,'sell','2023-08-28',dict(self.asset,kind='etf'),cfg)['stamp'],0)
    def test_next_close_is_also_delayed(self):
        self.cfg=replace(self.cfg,execution='close')
        r=self.simulate([1,0]+[0]*8);self.assertEqual(r['trades'][0]['date'],self.days[1])
    def test_future_mutation_does_not_change_past(self):
        b=fixture();a=b['assets']['600031.SH'];d=b['calendar'];c=Config()
        old=simulate(a,d,signals(a['bars'],20,60),c)['curve']
        for bar in a['bars'][400:]:
            for key in ('open','close','high','low','adjusted_close'):bar[key]*=2
        new=simulate(a,d,signals(a['bars'],20,60),c)['curve']
        self.assertEqual(old[:400],new[:400])
    def test_deliberate_future_signal_has_different_outcome(self):
        import random
        rng=random.Random(812)
        b=fixture(100);a=b['assets']['600031.SH'];d=b['calendar'];price=10
        for bar in a['bars']:
            price*=1+rng.choice([-.03,.03])
            bar.update(open=price,close=price,adjusted_close=price,low=price*.95,high=price*1.05)
        honest=[rng.randrange(2) for _ in d]
        # Deliberately illegal oracle: at T it knows the T+1-to-T+2 return.
        leaked=[int(a['bars'][i+2]['close']>a['bars'][i+1]['close']) if i+2<len(d) else 0 for i in range(len(d))]
        c=replace(self.cfg,capital=100000,execution='close')
        normal=simulate(a,d,honest,c);cheat=simulate(a,d,leaked,c)
        self.assertGreater(cheat['metrics']['total_return']-normal['metrics']['total_return'],.2)
    def test_flat_price_random_signal_cannot_profit_after_cost(self):
        import random
        rng=random.Random(42)
        for _ in range(30):
            r=self.simulate([rng.randrange(2) for _ in self.days])
            self.assertLessEqual(r['metrics']['total_return'],0)
    def test_adjusted_price_ratio_cannot_invent_shares(self):
        for bar in self.asset['bars']:bar['adjusted_close']=bar['close']*40+150
        r=self.simulate([1]*10)
        self.assertTrue(all(p['quantity']==900 for p in r['curve'][1:]))
        self.assertEqual(r['events'],[])
    def test_verified_corporate_action_and_dividend_pay_date(self):
        self.asset['corporate_actions_verified']=True
        self.asset['corporate_actions']={self.days[2]:dict(split_ratio=2,cash_per_share=.1,payment_date=self.days[4])}
        for bar in self.asset['bars'][2:]:bar.update(open=4.95,close=4.95,high=5.05,low=4.85,adjusted_close=10)
        r=self.simulate([1,0]+[0]*8)
        self.assertEqual(r['trades'][1]['reason'],'corporate_action_limits_missing')
        self.assertTrue(r['events']);self.assertEqual(r['curve'][2]['quantity'],1800)
        self.assertEqual(r['curve'][2]['cash'],995)
        self.assertAlmostEqual(r['curve'][2]['equity'],9995)
        self.assertAlmostEqual(r['curve'][4]['equity']-r['curve'][3]['equity'],0)

class ExperimentTests(unittest.TestCase):
    def test_deterministic_replay_and_partition(self):
        b=fixture();cfg=Config(random_runs=20)
        r=run(b,cfg);self.assertEqual(r,run(copy.deepcopy(b),cfg))
        self.assertEqual(r['parameter_groups_this_run'],9)
        self.assertTrue(all(x['period']=='train_only' for x in r['sensitivity']))
        self.assertLess(r['train']['curve'][-1]['date'],r['holdout']['curve'][0]['date'])
        for w in r['walk_forward']:self.assertLess(w['train_end'],w['test_start'])
    def test_too_short_fails(self):
        with self.assertRaises(ValueError):run(fixture(100))
    def test_matched_random_is_reproducible(self):
        b=fixture(150,True);a=b['assets']['600031.SH'];d=b['calendar'];c=Config(slippage=0,random_runs=30)
        targets=[int(10<=i<20 or 40<=i<55) for i in range(150)]
        primary=simulate(a,d,targets,c,1,150)
        r=random_test(a,d,primary,c,1,150)
        self.assertEqual(r,random_test(a,d,primary,c,1,150));self.assertEqual(r['matched_runs'],30)

if __name__=='__main__':unittest.main()
