from pathlib import Path
import unittest
import numpy as np
import pandas as pd
from app import indicators,technical

FIXTURES=Path(__file__).resolve().parent/'fixtures/ta-lib'


class IndicatorTests(unittest.TestCase):
    def test_talib_published_rsi_atr_and_bollinger_reference(self):
        for name,mapping in [('rsi',{'rsi14':'outReal'}),('atr',{'atr14':'outReal'}),
            ('bbands',{'boll_upper':'outRealUpperBand','ma20':'outRealMiddleBand','boll_lower':'outRealLowerBand'})]:
            f=pd.read_csv(FIXTURES/(name+'.csv'),comment='#');calculated=indicators.calculate(f)
            for ours,theirs in mapping.items():
                mask=f[theirs].notna()
                np.testing.assert_allclose(calculated.loc[mask,ours],f.loc[mask,theirs],rtol=1e-10,atol=1e-9,err_msg=name+' '+ours)

    def test_talib_published_macd_reference(self):
        f=pd.read_csv(FIXTURES/'macd.csv',comment='#');calculated=indicators.calculate(f)
        for ours,theirs in [('macd','outMACD'),('macd_signal','outMACDSignal'),('macd_hist','outMACDHist')]:
            mask=f[theirs].notna();np.testing.assert_allclose(calculated.loc[mask,ours],f.loc[mask,theirs],atol=1e-10)

    def test_future_prices_cannot_change_past_indicators_or_signals(self):
        f=pd.read_csv(FIXTURES/'atr.csv',comment='#');original=indicators.calculate(f)
        altered=f.copy();altered.loc[180:,['open','close','high','low']]*=3;altered.loc[180:,'volume']*=10
        changed=indicators.calculate(altered)
        pd.testing.assert_frame_equal(original.iloc[:180],changed.iloc[:180])
        for key,signal in indicators.events(original).items():pd.testing.assert_series_equal(signal.iloc[:180],indicators.events(changed)[key].iloc[:180])

    def test_levels_and_volume_exclude_current_observation(self):
        f=pd.DataFrame({'open':[10.]*30,'high':[11.]*30,'low':[9.]*30,'close':[10.]*30,'volume':[100.]*30})
        f.loc[29,['open','high','low','close','volume']]=[50,60,40,55,1000]
        result=indicators.calculate(f)
        self.assertEqual(result.loc[29,'prior_high20'],11)
        self.assertEqual(result.loc[29,'prior_low20'],9)
        self.assertEqual(result.loc[29,'volume_ratio20'],10)

    def test_missing_session_resets_wilder_and_warmup_is_not_zero(self):
        f=pd.read_csv(FIXTURES/'atr.csv',comment='#');f.loc[60,['open','high','low','close','volume']]=np.nan
        r=indicators.calculate(f)
        self.assertTrue(r['atr14'].iloc[:14].isna().all())
        self.assertTrue(r['rsi14'].iloc[60:75].isna().all())
        self.assertTrue(r['ma20'].iloc[60:80].isna().all())
        self.assertTrue(np.isfinite(r['rsi14'].iloc[75]))

    def test_flat_rsi_is_neutral(self):
        f=pd.DataFrame({'open':[10.]*40,'high':[10.]*40,'low':[10.]*40,'close':[10.]*40,'volume':[100.]*40})
        r=indicators.calculate(f);self.assertEqual(r['rsi14'].iloc[-1],50);self.assertEqual(r['atr14'].iloc[-1],0)

    def test_nonoverlapping_event_selection(self):
        positions=indicators.spaced_events(pd.Series([True]*65),20)
        self.assertEqual(positions,[0,20,40,60])

    def test_missing_holding_session_rejects_event_outcome(self):
        stock=pd.Series(np.arange(100.,141.));benchmark=pd.Series([100.]*41)
        self.assertAlmostEqual(technical.outcome(stock,benchmark,0)['excess_pct'],20)
        stock.iloc[10]=np.nan;self.assertIsNone(technical.outcome(stock,benchmark,0))

    def test_train_outcomes_do_not_cross_holdout_boundary(self):
        f=pd.read_csv(FIXTURES/'atr.csv',comment='#')
        f=pd.concat([f,f,f],ignore_index=True);frame=indicators.calculate(f)
        _,events=technical.event_study(frame,f['close'],f['close'],500)
        self.assertTrue(events)
        for event in events:
            if event['period']=='train':self.assertLess(event['end_date'],500)
            else:self.assertGreaterEqual(event['date'],500)

    def test_explicit_ema_and_cross_gap_continuous_indicators(self):
        f=pd.read_csv(FIXTURES/'atr.csv',comment='#').iloc[:120].copy()
        f.loc[60,['open','high','low','close','volume']]=np.nan
        r=indicators.calculate(f)
        self.assertIn('ema20',r.columns)
        self.assertIn('ema60',r.columns)
        # Strict columns reset after missing session 60, while c_* columns continue cleanly.
        self.assertTrue(r['ma20'].iloc[60:80].isna().all())
        self.assertTrue(r['ema20'].iloc[60:80].isna().all())
        self.assertTrue(r['macd'].iloc[60:86].isna().all())
        self.assertTrue(np.isnan(r['c_ma20'].iloc[60]))
        self.assertTrue(np.isfinite(r['c_ma20'].iloc[61]))
        self.assertTrue(np.isfinite(r['c_ema20'].iloc[61]))
        self.assertTrue(np.isfinite(r['c_macd'].iloc[61]))
        self.assertTrue(np.isfinite(r['c_macd_signal'].iloc[61]))

        cont=indicators.calculate(f,continuous=True)
        pd.testing.assert_series_equal(cont['ma20'],r['c_ma20'],check_names=False)
        pd.testing.assert_series_equal(cont['ema20'],r['c_ema20'],check_names=False)
        pd.testing.assert_series_equal(cont['macd'],r['c_macd'],check_names=False)

    def test_series_indicators_is_json_safe_and_complete(self):
        import json
        rows=[{'event_time':f'2026-01-{i+1:02d}T15:00:00+08:00','close':10.0+i*0.1} for i in range(40)]
        rows[20]['close']=None
        meta=indicators.series_indicators(rows)
        self.assertTrue(meta['continuous'])
        self.assertIsNotNone(meta['latest']['ema20'])
        self.assertIsNotNone(meta['latest']['macd'])
        self.assertIsNotNone(rows[-1]['ema12'])
        self.assertIsNotNone(rows[-1]['macd_hist'])
        dumped=json.dumps({'meta':meta,'rows':rows},allow_nan=False)
        self.assertIn('"ema20"',dumped)
