import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import core, screener, server

FETCH = '2026-11-18T09:00:00+00:00'  # 17:00 Shanghai, the 11-18 bar is final
# Accelerating trends: a linear ramp with SMA seeds reaches an exact steady state where DIF == DEA.
UP = [10 + i * .05 + i * i * .001 for i in range(200)]
DOWN = [80 - i * .05 - i * i * .001 for i in range(200)]


def kline(prices, end=date(2026, 11, 18)):
    rows, day = [], end
    days = []
    while len(days) < len(prices):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    for d, p in zip(reversed(days), prices):
        rows.append({'日期': d.isoformat(), '开盘': p, '收盘': p, '最高': p * 1.01, '最低': p * .99, '成交量': 1, '成交额': 1})
    return rows


class ScreenRuleTests(unittest.TestCase):
    def test_st_names(self):
        for name in ('ST泰禾', '*ST国华', 'S*ST前锋', 'st testing', '退市海越', '海越退'):
            self.assertTrue(screener.is_st(name), name)
        for name in ('贵州茅台', '招商银行', '中国平安'):
            self.assertFalse(screener.is_st(name), name)

    def test_industry_band_uses_positive_non_st_peers(self):
        stocks = [dict(name=f'银行{i}', industry='银行', pe_ttm=pe) for i, pe in enumerate([5, 6, 7, 8, 9])]
        stocks += [dict(name='ST银行', industry='银行', pe_ttm=500), dict(name='亏损银行', industry='银行', pe_ttm=-30),
                   dict(name='小行业A', industry='小', pe_ttm=10), dict(name='小行业B', industry='小', pe_ttm=12)]
        stats = screener.industry_stats(stocks)
        bank = stats['银行']
        self.assertEqual(bank['n'], 5)
        self.assertAlmostEqual(bank['mean'], 7)
        self.assertAlmostEqual(bank['std'], 1.5811, places=3)
        self.assertAlmostEqual(bank['low'], 7 - 2 * 1.5811, places=3)
        checks, _ = screener.fundamental_checks(dict(name='某行', industry='银行', pe_ttm=9.9, market_cap=6e10), stats)
        self.assertTrue(checks['pe_band'])
        checks, _ = screener.fundamental_checks(dict(name='某行', industry='银行', pe_ttm=10.5, market_cap=6e10), stats)
        self.assertFalse(checks['pe_band'])
        self.assertIsNone(stats['小']['low'])  # fewer than MIN_PEERS: cannot judge
        checks, _ = screener.fundamental_checks(dict(name='小A', industry='小', pe_ttm=10, market_cap=6e10), stats)
        self.assertIsNone(checks['pe_band'])
        checks, _ = screener.fundamental_checks(dict(name='某行', industry='银行', pe_ttm=8, market_cap=5e10), stats)
        self.assertFalse(checks['cap'])  # strictly greater than 500亿

    def test_technical_rules(self):
        up = screener.technicals(core.normalize(r, 'X', '1d', FETCH)[0] for r in kline(UP))
        self.assertEqual(screener.technical_checks(up), {'ma': True, 'ema': True, 'macd': True})
        self.assertEqual(up['bar_date'], '2026-11-18')
        down = screener.technicals(core.normalize(r, 'X', '1d', FETCH)[0] for r in kline(DOWN))
        self.assertEqual(screener.technical_checks(down), {'ma': False, 'ema': False, 'macd': False})
        short = screener.technicals(core.normalize(r, 'X', '1d', FETCH)[0] for r in kline([10] * 30))
        self.assertIsNone(short)
        self.assertEqual(screener.technical_checks(short), {'ma': None, 'ema': None, 'macd': None})

    def test_ma_rule_is_either_or(self):
        t = dict(ma5=10, ma10=11, ma20=9, ema12=5, ema20=6, ema50=4, dif=.1, dea=.2)
        c = screener.technical_checks(t)
        self.assertTrue(c['ma']); self.assertTrue(c['ema']); self.assertFalse(c['macd'])

    def test_band_toggle(self):
        checks = dict(not_st=True, cap=True, pe_positive=True, pe_band=False, ma=True, ema=True, macd=True)
        self.assertFalse(screener.passes(checks, True))
        self.assertTrue(screener.passes(checks, False))
        self.assertTrue(screener.passes(dict(checks, pe_band=None), True))
        self.assertFalse(screener.passes(dict(checks, pe_band=True, ma=None), True))


class ScreenRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(core, 'DATA', Path(self.tmp.name)); self.patch.start(); core.init()
        stocks = [  # symbol, name, cap, pe, industry, trend
            ('600001', '上涨大盘', 8e10, 10, '银行', 'up'),
            ('600002', '下跌大盘', 9e10, 9, '银行', 'down'),
            ('600003', 'ST大盘', 9e10, 8, '银行', 'up'),
            ('600004', '小盘上涨', 1e10, 7, '银行', 'up'),
            ('600005', '亏损大盘', 9e10, -5, '银行', 'up'),
            ('600006', '高估大盘', 7e10, 60, '银行', 'up'),
        ] + [(f'60010{i}', f'同业{i}', 1e10, 8 + i * .5, '银行', 'up') for i in range(6)]
        self.trend = {f'{c}.SH': t for c, _, _, _, _, t in stocks}
        with core.db() as c:
            c.execute("UPDATE instruments SET active=0")
            for code, name, cap, pe, ind, _ in stocks:
                sym = code + '.SH'
                c.execute('INSERT OR REPLACE INTO instruments VALUES(?,?,?,?,?,?,?,1)', (sym, code, 'SH', name, 'stock', 'test', FETCH))
                c.execute('INSERT INTO market_quotes VALUES(?,?,?,?)', (sym, core.dumps(dict(symbol=sym, close=10, pct_change_source=1, fetched_at=FETCH,
                          event_time=None, market_cap=cap, pe_ttm=pe, industry=ind)), 'b', FETCH))

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def fake(self, item, params):
        self.calls.append((item['symbol'], params['adjust']))
        if self.trend[item['symbol']] == 'up':
            return kline(UP)
        return kline(DOWN)

    def test_run_persists_funnel_and_evidence(self):
        self.calls = []
        with patch.object(core, 'now', return_value=datetime.fromisoformat(FETCH)), patch.object(screener.history_source, 'fetch', side_effect=self.fake), \
                patch.object(screener.time, 'sleep'):
            out = screener.run(refresh=False, update_bars=False)
        self.assertEqual(out['status'], 'ok')
        # only non-ST stocks above 500亿 are fetched, and with qfq prices
        self.assertEqual(sorted(s for s, _ in self.calls), ['600001.SH', '600002.SH', '600005.SH', '600006.SH'])
        self.assertTrue(all(a == 'qfq' for _, a in self.calls))
        view = screener.view(screener.latest_payload(), True)
        self.assertEqual([i['symbol'] for i in view['items'] if i['passed']], ['600001.SH'])
        self.assertEqual([f['count'] for f in view['funnel']], [12, 11, 4, 3, 2, 1, 1, 1])
        loose = screener.view(screener.latest_payload(), False)
        self.assertEqual(sorted(i['symbol'] for i in loose['items'] if i['passed']), ['600001.SH', '600006.SH'])
        self.assertTrue(loose['funnel'][4]['skipped'])
        payload = screener.latest_payload()
        self.assertEqual(payload['as_of'], '2026-11-18')
        with core.db() as c:
            batch = c.execute("SELECT * FROM batches WHERE symbol='__SCREEN__'").fetchone()
        self.assertEqual(batch['status'], 'ok')
        raw = core.read_raw(core.DATA / batch['raw_path'])
        self.assertEqual(set(raw['series']), {'600001.SH', '600002.SH', '600005.SH', '600006.SH'})
        with TestClient(server.app) as client:
            body = client.get('/api/screen?industry_band=false').json()
            self.assertEqual(body['passed'], 2)
            self.assertEqual(client.post('/api/screen/run', json={}).status_code, 403)

    def test_fetch_failure_is_not_a_pass(self):
        def broken(item, params):
            raise RuntimeError('timeout')
        with patch.object(core, 'now', return_value=datetime.fromisoformat(FETCH)), patch.object(screener.history_source, 'fetch', side_effect=broken), \
                patch.object(screener.time, 'sleep'):
            out = screener.run(refresh=False, update_bars=False)
        self.assertEqual(out['status'], 'partial'); self.assertEqual(out['passed'], 0); self.assertEqual(out['fetch_errors'], 4)

    def test_stale_and_short_history_are_not_judged(self):
        def fake(item, params):
            if item['symbol'] == '600001.SH':
                return kline(UP, end=date(2026, 11, 10))  # suspended since 11-10: uptrend but frozen
            if item['symbol'] == '600005.SH':
                return kline(UP[:30])
            return kline(UP)
        with patch.object(core, 'now', return_value=datetime.fromisoformat(FETCH)), patch.object(screener.history_source, 'fetch', side_effect=fake), \
                patch.object(screener.time, 'sleep'):
            out = screener.run(refresh=False, update_bars=False)
        self.assertEqual(out['fetch_errors'], 1); self.assertEqual(out['status'], 'partial')
        items = {i['symbol']: i for i in screener.view(screener.latest_payload(), False)['items']}
        self.assertTrue(items['600001.SH']['stale']); self.assertFalse(items['600001.SH']['passed'])
        self.assertIsNone(items['600001.SH']['checks']['ma']); self.assertIsNotNone(items['600001.SH']['technicals'])
        self.assertIn('日线不足', items['600005.SH']['fetch_error'])
        self.assertEqual(sorted(s for s, i in items.items() if i['passed']), ['600002.SH', '600006.SH'])

    def test_transient_timeout_is_retried(self):
        seen = {}
        def flaky(item, params):
            seen[item['symbol']] = seen.get(item['symbol'], 0) + 1
            # 600002 fails all in-line attempts and only succeeds in the final sequential pass
            if seen[item['symbol']] <= (3 if item['symbol'] == '600002.SH' else 1):
                raise screener.requests.ReadTimeout('read timed out')
            return kline(UP)
        with patch.object(core, 'now', return_value=datetime.fromisoformat(FETCH)), patch.object(screener.history_source, 'fetch', side_effect=flaky), \
                patch.object(screener.time, 'sleep'):
            out = screener.run(refresh=False, update_bars=False)
        self.assertEqual(out['status'], 'ok'); self.assertEqual(out['fetch_errors'], 0)
        self.assertEqual(seen, {'600001.SH': 2, '600002.SH': 4, '600005.SH': 2, '600006.SH': 2})
        self.assertNotIn('network_error', screener.latest_payload()['items'][0])

    def test_missing_fundamentals_rejected(self):
        with core.db() as c:
            c.execute("UPDATE market_quotes SET payload=json_remove(payload,'$.pe_ttm')")
        with self.assertRaisesRegex(RuntimeError, '市盈率TTM'):
            screener.run(refresh=False, update_bars=False)


if __name__ == '__main__':
    unittest.main()
