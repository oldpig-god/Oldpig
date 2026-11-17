import hashlib
import sqlite3
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from app import core
from scripts import migrate_bars


def legacy_row(symbol, interval, event_time, payload, batch, fetched):
    canonical = core.dumps(payload)
    return (symbol, interval, event_time, b'Z1' + zlib.compress(canonical.encode()), hashlib.sha256(canonical.encode()).hexdigest(), batch, fetched)


class MigrateBarsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(core, 'DATA', Path(self.tmp.name)); self.patch.start()
        con = sqlite3.connect(Path(self.tmp.name) / 'workbench.sqlite')
        con.executescript('''CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE observations (symbol TEXT, interval TEXT, event_time TEXT, payload TEXT, value_hash TEXT,
            batch_id TEXT, fetched_at TEXT, PRIMARY KEY(symbol, interval, event_time));
          CREATE TABLE revisions (id INTEGER PRIMARY KEY, symbol TEXT, interval TEXT, event_time TEXT, payload TEXT,
            value_hash TEXT, batch_id TEXT, fetched_at TEXT);''')
        daily = core.normalize({'日期': '2026-11-09', '开盘': 4.1, '最高': 4.3, '最低': 4.0, '收盘': 4.2, '成交量': 120, '成交额': 504, '涨跌幅': 1.2}, '510300.SH', '1d', '2026-11-10T08:00:00+00:00')[0]
        minute = core.normalize({'时间': '2026-11-10 14:40:00', '开盘': 0, '收盘': 4.21, '成交量': None}, '510300.SH', '1m', '2026-11-10T07:00:00+00:00')[0]
        odd = dict(daily, event_time='2026-11-06T15:00:00+08:00', timestamp=daily['timestamp'] - 3 * 86400000, source='GitHub', note='kept')
        self.payloads = [daily, minute, odd]
        rows = [legacy_row(p['symbol'], p['interval'], p['event_time'], p, 'b1' if p is not minute else 'b2', '2026-11-10T08:00:00+00:00') for p in self.payloads]
        con.executemany('INSERT INTO observations VALUES(?,?,?,?,?,?,?)', rows)
        # first revision duplicates the current value (old behaviour); second one is a genuinely superseded version
        con.execute('INSERT INTO revisions(symbol,interval,event_time,payload,value_hash,batch_id,fetched_at) VALUES(?,?,?,?,?,?,?)',
                    legacy_row('510300.SH', '1d', daily['event_time'], dict(daily, close=4.0), 'b0', 'x'))
        con.execute('INSERT INTO revisions(symbol,interval,event_time,payload,value_hash,batch_id,fetched_at) VALUES(?,?,?,?,?,?,?)', rows[0])
        con.commit(); con.close()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def run_script(self, *args):
        with patch.object(sys, 'argv', ['migrate_bars', *args]):
            migrate_bars.main()

    def test_legacy_database_requires_migration(self):
        with self.assertRaises(core.MigrationRequired):
            core.init()

    def test_dry_run_writes_nothing_and_apply_is_lossless(self):
        self.run_script()
        with self.assertRaises(core.MigrationRequired):
            core.init()
        self.run_script('--apply', '--vacuum')
        core.init()
        with patch.object(core, 'history_start', return_value='2021-11-10'):
            daily = core.observations('510300.SH', '1d'); minute = core.observations('510300.SH', '1m')
        strip = lambda r: {k: v for k, v in r.items() if k not in ('batch_id', 'fetched_at')}
        self.assertEqual([strip(r) for r in daily], [self.payloads[2], self.payloads[0]])
        self.assertEqual([strip(r) for r in minute], [self.payloads[1]])
        self.assertEqual(minute[0]['flags'], ['open_absent_in_trend_payload']); self.assertEqual(minute[0]['batch_id'], 'b2')
        with core.db() as c:
            self.assertEqual([r['batch_id'] for r in c.execute('SELECT batch_id FROM revisions')], ['b0'])
            self.assertIsNotNone(c.execute("SELECT extra FROM bars WHERE extra IS NOT NULL").fetchone())
            self.assertEqual(c.execute("SELECT rows FROM series_stats WHERE interval='1d'").fetchone()[0], 2)
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone())


if __name__ == '__main__':
    unittest.main()
