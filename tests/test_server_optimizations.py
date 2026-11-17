import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import core, server

FETCH = '2026-11-10T06:45:00+00:00'
SYMBOL = '510300.SH'


def bar_row(**changes):
    row = {
        '日期': '2026-11-09',
        '时间': '2026-11-10 14:40:00',
        '开盘': 4.1,
        '最高': 4.3,
        '最低': 4.0,
        '收盘': 4.2,
        '成交量': 120,
        '成交额': 504,
    }
    row.update(changes)
    return row


class ServerOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(core, 'DATA', Path(self.temp.name))
        self.data_patch.start()
        core.init()
        with core.db() as c:
            c.execute(
                'INSERT INTO metadata VALUES(?,?)',
                ('calendar', core.dumps({'dates': ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-12']})),
            )

    def tearDown(self):
        self.data_patch.stop()
        self.temp.cleanup()

    def publish(self, rows, interval='1d', fetched_at=FETCH):
        batch_id = core.begin_batch(SYMBOL, interval, 'fixture', {})
        return core.publish(batch_id, rows, fetched_at)

    def test_calendar_cache_hit_and_automatic_invalidation(self):
        # Cold load populates the cache and computes calendar state accurately.
        trading_dt = datetime.fromisoformat('2026-11-10T10:00:00+08:00')
        closed_dt = datetime.fromisoformat('2026-11-11T10:00:00+08:00')
        with patch.object(core.json, 'loads', wraps=core.json.loads) as loads_spy:
            dates = core.calendar_dates()
            self.assertEqual(dates, ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-12'])
            self.assertEqual(loads_spy.call_count, 1)

            # Mutating returned copy must not corrupt cached dates.
            dates.append('2099-01-01')

            # Cache hits: subsequent calendar_dates() / calendar_state() calls must not re-parse JSON.
            self.assertEqual(core.calendar_dates(), ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-12'])
            st_trade = core.calendar_state(trading_dt)
            st_closed = core.calendar_state(closed_dt)
            st_first = core.calendar_state(datetime.fromisoformat('2026-11-06T09:00:00+08:00'))
            st_oob = core.calendar_state(datetime.fromisoformat('2026-11-13T10:00:00+08:00'))
            self.assertEqual(loads_spy.call_count, 1)

        self.assertEqual(st_trade['state'], 'trading')
        self.assertEqual(st_trade['previous_trade_date'], '2026-11-09')
        self.assertEqual(st_trade['expected_daily'], '2026-11-09')
        self.assertEqual(st_closed['state'], 'closed')
        self.assertEqual(st_closed['label'], '休市')
        self.assertEqual(st_closed['previous_trade_date'], '2026-11-10')
        self.assertEqual(st_first['state'], 'preopen')
        self.assertIsNone(st_first['previous_trade_date'])
        self.assertEqual(st_oob['state'], 'unknown_calendar')

        # Updating metadata calendar invalidates cache automatically.
        with core.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO metadata VALUES('calendar',?)",
                (core.dumps({'dates': ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12']}),),
            )
        self.assertEqual(core.calendar_dates(), ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12'])
        self.assertEqual(core.calendar_state(closed_dt)['state'], 'trading')

        # Passing an explicit connection reuses it without opening a second db() context.
        with core.db() as c:
            with patch.object(core, 'db', wraps=core.db) as db_spy:
                self.assertEqual(core.calendar_state(closed_dt, c=c)['state'], 'trading')
                self.assertEqual(len(core.calendar_dates(c=c)), 5)
                db_spy.assert_not_called()

        # Patching core.DATA to a different directory invalidates cache automatically.
        with tempfile.TemporaryDirectory() as other_dir:
            with patch.object(core, 'DATA', Path(other_dir)):
                core.init()
                self.assertEqual(core.calendar_dates(), [])
                self.assertEqual(core.calendar_state(trading_dt)['state'], 'unknown_calendar')
                with core.db() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO metadata VALUES('calendar',?)",
                        (core.dumps({'dates': ['2026-11-02', '2026-11-03']}),),
                    )
                self.assertEqual(core.calendar_dates(), ['2026-11-02', '2026-11-03'])

        # Switching back to the original core.DATA also re-validates against its metadata.
        self.assertEqual(core.calendar_dates(), ['2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12'])

    def test_universe_sort_modes_nulls_last_and_invalid_sort(self):
        with core.db() as c:
            keep = ('000001.SZ', '159915.SZ', '300750.SZ', '510300.SH', '600519.SH')
            c.execute(
                f"UPDATE instruments SET active = CASE WHEN symbol IN ({','.join('?' * len(keep))}) THEN 1 ELSE 0 END",
                keep,
            )
            quotes = {
                '000001.SZ': {'symbol': '000001.SZ', 'close': 11.2, 'pct_change_source': -2.5, 'amount_raw': 300000000.0, 'event_time': '2026-11-10T14:40:00+08:00', 'fetched_at': FETCH},
                '600519.SH': {'symbol': '600519.SH', 'close': 1500.0, 'pct_change_source': 4.2, 'amount_raw': 1000000000.0, 'event_time': '2026-11-10T14:40:00+08:00', 'fetched_at': FETCH},
                '300750.SZ': {'symbol': '300750.SZ', 'close': 240.0, 'pct_change_source': 0.0, 'amount_raw': 500000000.0, 'event_time': '2026-11-10T14:40:00+08:00', 'fetched_at': FETCH},
                # Explicit JSON NULLs in quote payload (must sort after non-nulls, tie-broken by symbol).
                '510300.SH': {'symbol': '510300.SH', 'close': 4.2, 'pct_change_source': None, 'amount_raw': None, 'event_time': None, 'fetched_at': FETCH},
                # 159915.SZ intentionally has no market_quotes row at all (SQL NULL payload).
            }
            for sym, payload in quotes.items():
                c.execute(
                    'INSERT INTO market_quotes VALUES(?,?,?,?)',
                    (sym, core.dumps(payload), 'batch-q', FETCH),
                )
            rows = c.execute('SELECT symbol, name FROM instruments WHERE active=1').fetchall()
            expected_by_name = [r['symbol'] for r in sorted(rows, key=lambda r: (r['name'], r['symbol']))]

        with TestClient(server.app) as client:
            symbols = lambda qs: [item['symbol'] for item in client.get('/api/universe?' + qs).json()['items']]

            self.assertEqual(symbols('sort=code'), ['000001.SZ', '159915.SZ', '300750.SZ', '510300.SH', '600519.SH'])
            self.assertEqual(symbols('sort=name'), expected_by_name)
            self.assertEqual(symbols('sort=pct_desc'), ['600519.SH', '300750.SZ', '000001.SZ', '159915.SZ', '510300.SH'])
            self.assertEqual(symbols('sort=pct_asc'), ['000001.SZ', '300750.SZ', '600519.SH', '159915.SZ', '510300.SH'])
            self.assertEqual(symbols('sort=amount_desc'), ['600519.SH', '300750.SZ', '000001.SZ', '159915.SZ', '510300.SH'])

            bad = client.get('/api/universe?sort=bad')
            self.assertEqual(bad.status_code, 400)

    def test_series_single_connection_execution_and_payload_fields(self):
        self.publish([bar_row()], interval='1d')
        self.publish([bar_row(开盘=0)], interval='1m')
        with core.db() as c:
            c.execute(
                'INSERT INTO market_quotes VALUES(?,?,?,?)',
                (
                    SYMBOL,
                    core.dumps({'symbol': SYMBOL, 'close': 4.21, 'pct_change_source': 0.5, 'event_time': '2026-11-10T14:40:00+08:00', 'fetched_at': FETCH}),
                    'batch-quote',
                    FETCH,
                ),
            )

        with core.db() as c:
            with patch.object(core, 'db', wraps=core.db) as db_spy:
                inst = core.instrument(SYMBOL, c=c)
                obs = core.observations(SYMBOL, '1d', c=c)
                fresh = core.freshness(obs[-1], '1d', c=c)
                db_spy.assert_not_called()
        self.assertEqual(inst['symbol'], SYMBOL)
        self.assertEqual(len(obs), 1)
        self.assertIn('state', fresh)

        with TestClient(server.app) as client:
            with patch.object(core, 'db', wraps=core.db) as db_spy:
                resp_1d = client.get(f'/api/series/{SYMBOL}?interval=1d')
                self.assertEqual(resp_1d.status_code, 200)
                self.assertEqual(db_spy.call_count, 1)

                db_spy.reset_mock()
                resp_1m = client.get(f'/api/series/{SYMBOL}?interval=1m')
                self.assertEqual(resp_1m.status_code, 200)
                self.assertEqual(db_spy.call_count, 1)

        data = resp_1d.json()
        expected_keys = {
            'instrument', 'interval', 'chart_type', 'history_window', 'records',
            'quote', 'range', 'freshness', 'source', 'github_available',
            'adjustment', 'volume_unit', 'last_batch', 'limitations',
        }
        self.assertTrue(expected_keys.issubset(data.keys()))
        self.assertEqual(data['instrument']['symbol'], SYMBOL)
        self.assertEqual(data['interval'], '1d')
        self.assertEqual(data['chart_type'], 'candlestick')
        self.assertEqual(data['records'][0]['close'], 4.2)
        self.assertEqual(data['quote']['close'], 4.21)
        self.assertEqual(data['range']['count'], 1)
        self.assertIn('state', data['freshness'])
        self.assertEqual(data['last_batch']['symbol'], SYMBOL)

        data_1m = resp_1m.json()
        self.assertEqual(data_1m['chart_type'], 'price_line')
        self.assertEqual(data_1m['records'][0]['close'], 4.2)
        self.assertNotIn('open', data_1m['records'][0])

    def test_batch_detail_returns_parsed_issues_with_and_without_raw_file(self):
        published = self.publish([bar_row(), bar_row(日期='2026-11-08', 收盘=0)], interval='1d')
        batch_id = published['batch_id']
        empty_batch = core.begin_batch(SYMBOL, '1d', 'fixture', {})
        core.fail_batch(empty_batch, 'timeout')

        with TestClient(server.app) as client:
            resp_present = client.get('/api/batches/' + batch_id)
            self.assertEqual(resp_present.status_code, 200)
            body_present = resp_present.json()
            self.assertEqual(body_present['parsed_issues'], {'invalid_close': 1})
            self.assertFalse(body_present.get('raw_missing', False))
            self.assertEqual(len(body_present['raw_sample']), 2)
            self.assertEqual(len(body_present['issue_examples']), 1)

            raw_file = core.DATA / body_present['raw_path']
            raw_file.unlink()

            resp_missing = client.get('/api/batches/' + batch_id)
            self.assertEqual(resp_missing.status_code, 200)
            body_missing = resp_missing.json()
            self.assertTrue(body_missing['raw_missing'])
            self.assertEqual(body_missing['parsed_issues'], {'invalid_close': 1})
            self.assertEqual(body_missing['issue_examples'], [])
            self.assertEqual(body_missing['raw_sample'], [])

            resp_no_raw = client.get('/api/batches/' + empty_batch)
            self.assertEqual(resp_no_raw.status_code, 200)
            self.assertEqual(resp_no_raw.json()['parsed_issues'], {})

    def test_frontend_static_contracts(self):
        static_dir = core.ROOT / 'app' / 'static'
        index_html = (static_dir / 'index.html').read_text(encoding='utf-8')
        app_js = (static_dir / 'app.js').read_text(encoding='utf-8')
        research_html = (static_dir / 'research' / 'index.html').read_text(encoding='utf-8')
        research_js = (static_dir / 'research' / 'research.js').read_text(encoding='utf-8')

        # Main workbench HTML contracts (existing check_market_ui.py + new controls)
        for token in (
            '协方差与相关性 ↗',
            'href="/static/research/index.html"',
            'id="session-wrap"',
            'id="session-day"',
            'id="watch-sort"',
            'id="sort"',
            'id="open"',
            'id="high"',
            'id="low"',
            'id="tick-time"',
            'id="price"',
            'id="page-info"',
            'id="prev-page"',
            'id="next-page"',
            'id="exchange"',
            'id="search"',
            'id="symbol"',
            'id="snapshot-summary"',
            'id="coverage-daily"',
            'id="asset-count"',
            'id="chart"',
            'id="ma"',
            'id="history-source"',
            'id="freshness"',
            'id="range"',
            'id="history-origin"',
            'id="history-audit"',
            'id="adjustment"',
            'id="quality-rows"',
            'id="batch-evidence"',
            'id="theme"',
            'data-filter="etf"',
            'data-interval="1d"',
            'data-interval="5m"',
            'data-interval="1m"',
            'data-tab="quality"',
            'data-tab="bars"',
        ):
            self.assertIn(token, index_html)
        for sort_val in ('value="code"', 'value="pct_desc"', 'value="pct_asc"', 'value="amount_desc"', 'value="name"'):
            self.assertIn(sort_val, index_html)

        # Main workbench JS contracts
        for token in (
            '最新批量快照',
            '#session-day',
            '#session-wrap',
            '#watch-sort',
            '#page-info',
            "'open'",
            'data-close-evidence',
            'is-1m',
        ):
            self.assertIn(token, app_js)
        self.assertNotIn('pageTotal.toLocaleString()', app_js)
        self.assertIsNone(re.search(r'\.gl-fact-ohlc[^\n;]*\.hidden\s*=', app_js))

        # Research page HTML contracts (check_research_ui.py + theme toggle)
        for token in (
            'id="research-theme"',
            'data-theme="dark"',
            'gl-theme',
            'id="samples"',
            'id="heatmap"',
            'id="covariance-heatmap"',
            'id="matrix"',
            'id="covariance"',
            'id="pair-detail"',
            'id="search-pair"',
            'id="sort"',
            'id="pairs"',
            'id="sensitivity"',
            'download',
        ):
            self.assertIn(token, research_html)

        # Research JS contracts
        for token in (
            'research-theme',
            'gl-theme',
            'is-selected',
            'samples',
            'heatmap',
            'covariance-heatmap',
            'matrix',
            'covariance',
            'pair-detail',
            'search-pair',
            'sort',
            'pairs',
            'sensitivity',
            'toLocaleString()',
            'toFixed(2)',
        ):
            self.assertIn(token, research_js)

        app_css = (static_dir / 'app.css').read_text(encoding='utf-8')
        research_css = (static_dir / 'research' / 'research.css').read_text(encoding='utf-8')
        self.assertIn('#watch-sort', app_css)
        self.assertIn('.gl-facts.is-1m', app_css)
        self.assertIn('.is-selected', research_css)


if __name__ == '__main__':
    unittest.main()
