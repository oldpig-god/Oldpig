"""One-time migration: legacy JSON `observations` -> columnar `bars` (lossless, verified row by row).

    python3 -m scripts.migrate_bars                   # dry run: decode + verify every row, write nothing
    python3 -m scripts.migrate_bars --apply           # convert, verify counts, drop legacy table
    python3 -m scripts.migrate_bars --apply --vacuum  # also shrink the file (needs free disk ~ new size x2)

Stop the web service and all collectors first. Every legacy payload is rebuilt from the new columns
and compared with the original dict; differences are stored verbatim in bars.extra, so nothing is lost.
Revision rows that merely duplicate the current value are removed (revisions now holds superseded
versions only); genuinely superseded versions are kept unchanged.
The legacy table is dropped only after the row count of bars matches it exactly.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import bars, core, process_lock  # noqa: E402

DUPLICATE_REVISIONS = '''DELETE FROM revisions WHERE id IN (SELECT r.id FROM revisions r
  JOIN observations o ON o.symbol=r.symbol AND o.interval=r.interval AND o.event_time=r.event_time AND o.value_hash=r.value_hash
  WHERE r.id BETWEEN ? AND ?
    AND NOT EXISTS (SELECT 1 FROM revisions n WHERE n.symbol=r.symbol AND n.interval=r.interval AND n.event_time=r.event_time AND n.id>r.id))'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--apply', action='store_true')
    p.add_argument('--vacuum', action='store_true')
    p.add_argument('--chunk', type=int, default=50_000)
    a = p.parse_args()
    locks = []
    try:
        for name in ('backfill.lock', 'collector.lock'):
            f = (core.DATA / name).open('a+b')
            locks.append(f)
            try:
                process_lock.acquire(f)
            except BlockingIOError:
                sys.exit(f'{name} is held: stop the running collector/backfill first')
        migrate(a)
    finally:
        for f in locks:
            f.close()


def migrate(a):
    path = core.DATA / 'workbench.sqlite'
    con = sqlite3.connect(path if a.apply else f'file:{path}?mode=ro', uri=not a.apply, timeout=60)
    con.row_factory = sqlite3.Row
    if a.apply:
        con.executescript('PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA cache_size=-262144;PRAGMA temp_store=MEMORY;')
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'observations' not in tables:
        sys.exit('No legacy observations table: nothing to migrate.')
    if con.execute("SELECT 1 FROM metadata WHERE key='storage'").fetchone():
        sys.exit('metadata.storage is already set; refusing to migrate twice.')
    total = con.execute('SELECT count(*) FROM observations').fetchone()[0]
    print(f'legacy rows: {total:,}  mode: {"APPLY" if a.apply else "dry run"}', flush=True)
    if a.apply:
        con.executescript(bars.SCHEMA)
        con.execute('DELETE FROM bars')  # restart cleanly if a previous attempt was interrupted
        con.execute('DELETE FROM batch_refs')
        con.commit()
    refs, stats = {}, dict(rows=0, extra=0, hash_mismatch=0, key_mismatch=0)
    examples = []
    last, start = 0, time.time()
    while True:
        chunk = con.execute('SELECT rowid,* FROM observations WHERE rowid>? ORDER BY rowid LIMIT ?', (last, a.chunk)).fetchall()
        if not chunk:
            break
        out, new_refs = [], []
        for r in chunk:
            payload = core.unpack_payload(r['payload'])
            key = (r['batch_id'], r['fetched_at'])
            if key not in refs:
                refs[key] = len(refs) + 1
                new_refs.append((refs[key],) + key)
            values = bars.from_payload(payload, core.SH, refs[key])
            if bars.to_payload(values, core.SH) != payload:
                raise SystemExit(f'Round-trip failure at rowid {r["rowid"]}: {payload}')
            if (payload.get('symbol'), payload.get('interval'), payload.get('event_time')) != (r['symbol'], r['interval'], r['event_time']):
                stats['key_mismatch'] += 1
                if len(examples) < 5:
                    examples.append(('key', r['symbol'], r['interval'], r['event_time']))
            if values[11] != bars.hash_from_hex(r['value_hash']):
                stats['hash_mismatch'] += 1
                if len(examples) < 5:
                    examples.append(('hash', r['symbol'], r['interval'], r['event_time']))
            stats['extra'] += values[13] is not None
            out.append(values)
        if stats['key_mismatch']:
            raise SystemExit(f'Primary key differs from payload identity; aborting: {examples}')
        if a.apply:
            with con:
                con.executemany('INSERT INTO batch_refs(id,batch_id,fetched_at) VALUES(?,?,?)', new_refs)
                con.executemany(f'INSERT INTO bars({bars.COLUMNS}) VALUES({",".join("?" * 14)})', out)
        stats['rows'] += len(chunk)
        last = chunk[-1]['rowid']
        rate = stats['rows'] / max(time.time() - start, 1e-6)
        print(f'  {stats["rows"]:,}/{total:,}  {rate:,.0f} rows/s  eta {(total - stats["rows"]) / rate:,.0f}s', flush=True)
    print(f'verified: {stats}  batch refs: {len(refs):,}', flush=True)
    if examples:
        print('examples:', examples, flush=True)
    if not a.apply:
        print('dry run OK: every payload round-trips exactly. Re-run with --apply to convert.', flush=True)
        con.close()
        return
    converted = con.execute('SELECT count(*) FROM bars').fetchone()[0]
    if converted != total:
        raise SystemExit(f'Count mismatch: bars {converted:,} != observations {total:,}; legacy table kept.')
    lo, hi = con.execute('SELECT coalesce(min(id),0),coalesce(max(id),0) FROM revisions').fetchone()
    removed = 0
    for first in range(lo, hi + 1, 500_000):
        with con:
            removed += con.execute(DUPLICATE_REVISIONS, (first, first + 499_999)).rowcount
    print(f'removed {removed:,} revision rows duplicating the current value', flush=True)
    with con:
        con.execute('DROP TABLE observations')
        con.execute("INSERT OR REPLACE INTO metadata VALUES('storage','bars_v1')")
        con.execute('CREATE TABLE IF NOT EXISTS series_stats (symbol TEXT,interval TEXT,first_time TEXT,last_time TEXT,rows INTEGER,PRIMARY KEY(symbol,interval))')
        core.rebuild_series_stats(con)
    print('legacy table dropped; storage=bars_v1', flush=True)
    if a.vacuum:
        print('VACUUM…', flush=True)
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        con.execute('VACUUM')
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    print(f'done in {time.time() - start:,.0f}s; file size {path.stat().st_size / 2**30:.2f} GiB', flush=True)


if __name__ == '__main__':
    main()
