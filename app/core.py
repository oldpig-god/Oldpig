"""Local research data store. Raw responses, versioned prices and explicit quality state."""
import bisect
import gzip
import zlib
import hashlib
import json
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from . import bars

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('FINANCE_DATA_DIR', ROOT / 'data'))
SH = ZoneInfo('Asia/Shanghai')
ASSETS = [
    ('510300','SH','沪深300ETF','etf'), ('159915','SZ','创业板ETF','etf'),
    ('510500','SH','中证500ETF','etf'), ('510880','SH','红利ETF','etf'),
    ('600519','SH','贵州茅台','stock'), ('000001','SZ','平安银行','stock'),
    ('600036','SH','招商银行','stock'), ('601318','SH','中国平安','stock'),
    ('000333','SZ','美的集团','stock'), ('300750','SZ','宁德时代','stock'),
]
UNIVERSE = {f'{c}.{e}': dict(symbol=f'{c}.{e}', code=c, exchange=e, name=n, kind=k) for c,e,n,k in ASSETS}


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat()


def dumps(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'workbench.sqlite', timeout=20)
    con.row_factory = sqlite3.Row
    # journal_mode=WAL is persistent and set once in init(). In WAL mode synchronous=NORMAL
    # keeps committed transactions durable against process crashes while avoiding an fsync
    # on every commit; the backfill queue commits several times per security.
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('PRAGMA busy_timeout=20000')
    con.execute('PRAGMA temp_store=MEMORY')
    con.execute('PRAGMA cache_size=-65536')
    con.execute('PRAGMA mmap_size=268435456')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


class MigrationRequired(RuntimeError):
    pass


def init():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        storage=c.execute("SELECT value FROM metadata WHERE key='storage'").fetchone() if 'metadata' in tables else None
        if 'observations' in tables and not storage:
            if c.execute('SELECT 1 FROM observations LIMIT 1').fetchone():
                raise MigrationRequired('数据库仍是旧版 JSON 价格存储，需要一次性迁移为列式 bars 表。'
                                        '请停止服务与全部采集进程后运行：python3 -m scripts.migrate_bars --apply '
                                        '（先不加 --apply 可只做无损校验演练）')
            c.execute('DROP TABLE observations')
        c.executescript(bars.SCHEMA+'''
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY, symbol TEXT, interval TEXT, endpoint TEXT, parameters TEXT,
          started_at TEXT, finished_at TEXT, status TEXT, raw_path TEXT, raw_sha256 TEXT,
          rows_in INTEGER DEFAULT 0, rows_accepted INTEGER DEFAULT 0, rows_rejected INTEGER DEFAULT 0,
          changed INTEGER DEFAULT 0, error TEXT, issues TEXT, source_version TEXT);
        CREATE TABLE IF NOT EXISTS revisions (
          id INTEGER PRIMARY KEY, symbol TEXT, interval TEXT, event_time TEXT, payload TEXT,
          value_hash TEXT, batch_id TEXT, fetched_at TEXT);
        CREATE INDEX IF NOT EXISTS revisions_lookup ON revisions(symbol,interval,event_time);
        CREATE TABLE IF NOT EXISTS watermarks (
          symbol TEXT, interval TEXT, event_time TEXT, batch_id TEXT,
          PRIMARY KEY(symbol, interval));
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS instruments (
          symbol TEXT PRIMARY KEY,code TEXT,exchange TEXT,name TEXT,kind TEXT,
          source TEXT,seen_at TEXT,active INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS instruments_kind ON instruments(active,kind,exchange,code);
        CREATE TABLE IF NOT EXISTS market_members (source TEXT,symbol TEXT,PRIMARY KEY(source,symbol));
        CREATE TABLE IF NOT EXISTS market_quotes (
          symbol TEXT PRIMARY KEY,payload TEXT,batch_id TEXT,fetched_at TEXT);
        CREATE TABLE IF NOT EXISTS history_tasks (
          symbol TEXT,interval TEXT,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,
          updated_at TEXT,batch_id TEXT,error TEXT,PRIMARY KEY(symbol,interval));
        CREATE INDEX IF NOT EXISTS history_tasks_status ON history_tasks(status,interval);
        CREATE INDEX IF NOT EXISTS batches_latest ON batches(symbol,interval,started_at DESC);
        CREATE TABLE IF NOT EXISTS series_stats (
          symbol TEXT,interval TEXT,first_time TEXT,last_time TEXT,rows INTEGER,
          PRIMARY KEY(symbol,interval));
        ''')
        c.executemany('INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?,?,1)',
                      [(r['symbol'],r['code'],r['exchange'],r['name'],r['kind'],'bootstrap',iso()) for r in UNIVERSE.values()])
        c.execute("INSERT OR IGNORE INTO metadata VALUES('storage','bars_v1')")
        # One-time summary build so coverage/health never aggregate millions of rows.
        if not c.execute("SELECT 1 FROM metadata WHERE key='series_stats_v2'").fetchone():
            rebuild_series_stats(c)


def ts_of(day):
    """'YYYY-MM-DD' (Shanghai) -> epoch seconds at local midnight."""
    return int(datetime.fromisoformat(day).replace(tzinfo=SH).timestamp())


def event_time(ts):
    return datetime.fromtimestamp(ts,SH).isoformat()


def rebuild_series_stats(c):
    c.execute('DELETE FROM series_stats')
    for r in c.execute('SELECT symbol,interval,min(ts) f,max(ts) l,count(*) n FROM bars GROUP BY symbol,interval').fetchall():
        c.execute('INSERT INTO series_stats VALUES(?,?,?,?,?)',(r['symbol'],r['interval'],event_time(r['f']),event_time(r['l']),r['n']))
    c.execute("INSERT OR REPLACE INTO metadata VALUES('series_stats_v2',?)",(iso(),))


def refresh_series_stats(c, symbol, interval):
    """Exact per-series summary, recomputed from the primary-key B-tree (one series)."""
    r=c.execute('SELECT min(ts) f,max(ts) l,count(*) n FROM bars WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
    if r['n']:c.execute('INSERT OR REPLACE INTO series_stats VALUES(?,?,?,?,?)',(symbol,interval,event_time(r['f']),event_time(r['l']),r['n']))
    else:c.execute('DELETE FROM series_stats WHERE symbol=? AND interval=?',(symbol,interval))


def universe():
    with db() as c:
        return {r['symbol']:dict(r) for r in c.execute('SELECT * FROM instruments WHERE active=1 ORDER BY symbol')}


def instrument(symbol, c=None):
    if c is None:
        with db() as conn:
            r = conn.execute('SELECT * FROM instruments WHERE symbol=?', (symbol,)).fetchone()
    else:
        r = c.execute('SELECT * FROM instruments WHERE symbol=?', (symbol,)).fetchone()
    return dict(r) if r else None


def history_start(at=None):
    day=(at or now()).astimezone(SH).date()
    try:return day.replace(year=day.year-5).isoformat()
    except ValueError:return day.replace(year=day.year-5,day=28).isoformat()


def history_window():
    return {'years':5,'start':history_start(),'end':now().astimezone(SH).date().isoformat(),'mode':'rolling_calendar_years'}


def unpack_payload(value):
    return json.loads(zlib.decompress(value[2:])) if isinstance(value,bytes) and value.startswith(b'Z1') else json.loads(value)


def read_raw(path):
    content=path.read_bytes()
    return json.loads(gzip.decompress(content) if path.suffix=='.gz' else content)

def numeric(value, positive=False, nonnegative=False):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or positive and n <= 0 or nonnegative and n < 0:
        return None
    return n


def normalize(row, symbol, interval, fetched_at):
    """1m trends are close observations. Only daily/5m can be validated OHLC bars."""
    problems = []
    try:
        stamp = str(row.get('日期') if interval == '1d' else row.get('时间'))
        dt = datetime.fromisoformat(stamp)
        if interval == '1d':
            dt = dt.replace(hour=15, minute=0, second=0, microsecond=0)
        dt = dt.replace(tzinfo=SH) if dt.tzinfo is None else dt.astimezone(SH)
    except (ValueError, TypeError):
        return None, ['invalid_time']
    fetched = datetime.fromisoformat(fetched_at).astimezone(SH)
    if dt > fetched:
        return None, ['unclosed_or_future_period']
    if interval == '1d' and dt.date() == fetched.date() and fetched.hour < 16:
        return None, ['daily_not_final_yet']
    if interval != '1d':
        minute = dt.hour * 60 + dt.minute
        if not (570 <= minute <= 690 or 780 <= minute <= 900):
            return None, ['outside_session']
    if dt.date().isoformat()<history_start(datetime.fromisoformat(fetched_at)):
        return None, ['outside_research_window']
    close = numeric(row.get('收盘'), positive=True)
    if close is None:
        return None, ['invalid_close']
    result = dict(symbol=symbol, interval=interval, event_time=dt.isoformat(),
                  timestamp=int(dt.timestamp()*1000), close=close, currency='CNY', adjustment='raw',
                  volume_raw=numeric(row.get('成交量'), nonnegative=True), volume_unit='source_unverified',
                  amount_raw=numeric(row.get('成交额'), nonnegative=True), source='东方财富 / AKShare',
                  pct_change_source=numeric(row.get('涨跌幅')), quality='price_only' if interval=='1m' else 'valid_ohlc')
    if interval == '1m':
        if numeric(row.get('开盘'), positive=True) is None:
            problems.append('open_absent_in_trend_payload')
        # No synthetic OHLC: price series remains useful even when open is a source placeholder.
    else:
        prices = {k:numeric(row.get(v), positive=True) for k,v in [('open','开盘'),('high','最高'),('low','最低')]}
        if any(v is None for v in prices.values()):
            return None, ['invalid_ohlc']
        if not prices['low'] <= min(prices['open'],close) <= max(prices['open'],close) <= prices['high']:
            return None, ['inconsistent_ohlc']
        result.update(prices)
    result['flags'] = problems
    return result, problems


def begin_batch(symbol, interval, endpoint, parameters, version=''):
    batch = uuid.uuid4().hex
    with db() as c:
        c.execute('INSERT INTO batches(id,symbol,interval,endpoint,parameters,started_at,status,source_version) VALUES(?,?,?,?,?,?,?,?)',
                  (batch,symbol,interval,endpoint,dumps(parameters),iso(),'running',version))
    return batch


def fail_batch(batch, message):
    with db() as c:
        c.execute('UPDATE batches SET status=?,error=?,finished_at=? WHERE id=?',('error',message[:700],iso(),batch))


def publish(batch, rows, fetched_at=None):
    fetched_at = fetched_at or iso()
    with db() as c:
        spec = dict(c.execute('SELECT * FROM batches WHERE id=?',(batch,)).fetchone())
    raw = dict(batch_id=batch,symbol=spec['symbol'],interval=spec['interval'],endpoint=spec['endpoint'],
               parameters=json.loads(spec['parameters']),fetched_at=fetched_at,source_version=spec['source_version'],rows=rows)
    raw_bytes=gzip.compress(dumps(raw).encode(),compresslevel=3,mtime=0)
    rel=Path('raw')/fetched_at[:10]/f'{batch}.json.gz'
    path=DATA/rel;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:
        f.write(raw_bytes);f.flush();os.fsync(f.fileno())
    accepted=[];issues={};rejected=0
    for row in rows:
        value,problems=normalize(row,spec['symbol'],spec['interval'],fetched_at)
        for issue in problems:issues[issue]=issues.get(issue,0)+1
        if value is None:rejected+=1
        else:accepted.append(value)
    # Conflicting repeated timestamps inside a response are quarantined, not arbitrarily last-wins.
    unique={};conflicts=set()
    for row in accepted:
        key=row['event_time']
        if key in unique and unique[key]!=row:conflicts.add(key)
        else:unique[key]=row
    for key in conflicts:
        unique.pop(key,None)
        issues['conflicting_timestamp']=issues.get('conflicting_timestamp',0)+1
    rejected+=sum(x['event_time'] in conflicts for x in accepted)
    accepted=sorted(unique.values(),key=lambda x:x['event_time'])
    changed=0
    symbol,interval=spec['symbol'],spec['interval']
    with db() as c:
        c.execute('INSERT OR IGNORE INTO batch_refs(batch_id,fetched_at) VALUES(?,?)',(batch,fetched_at))
        ref=c.execute('SELECT id FROM batch_refs WHERE batch_id=? AND fetched_at=?',(batch,fetched_at)).fetchone()[0]
        existing={}
        if accepted:
            existing={r['ts']:r['hash'] for r in c.execute(
                'SELECT ts,hash FROM bars WHERE symbol=? AND interval=? AND ts BETWEEN ? AND ?',
                (symbol,interval,accepted[0]['timestamp']//1000,accepted[-1]['timestamp']//1000))}
        superseded=[];upserts=[]
        for row in accepted:
            values=bars.from_payload(row,SH,ref)
            old=existing.get(values[2])
            if old!=values[11]:
                changed+=1
                if old is not None:superseded.append(values[2])
            upserts.append(values)
        # revisions keeps superseded versions only (legacy JSON payload); current value lives in bars.
        # Full history of a timestamp = revisions (older, by id) + bars (current).
        for ts in superseded:
            r=c.execute(f'SELECT b.{bars.COLUMNS.replace(",",",b.")},f.batch_id,f.fetched_at FROM bars b JOIN batch_refs f ON f.id=b.ref WHERE b.symbol=? AND b.interval=? AND b.ts=?',(symbol,interval,ts)).fetchone()
            canonical=dumps(bars.to_payload(r,SH))
            c.execute('INSERT INTO revisions(symbol,interval,event_time,payload,value_hash,batch_id,fetched_at) VALUES(?,?,?,?,?,?,?)',
                      (symbol,interval,event_time(ts),b'Z1'+zlib.compress(canonical.encode(),level=3),hashlib.sha256(canonical.encode()).hexdigest(),r['batch_id'],r['fetched_at']))
        c.executemany(f'INSERT OR REPLACE INTO bars({bars.COLUMNS}) VALUES({",".join("?"*14)})',upserts)
        if accepted:refresh_series_stats(c,symbol,interval)
        status='empty' if not rows else 'quarantined' if not accepted else 'partial' if rejected else 'ok'
        c.execute('UPDATE batches SET status=?,finished_at=?,raw_path=?,raw_sha256=?,rows_in=?,rows_accepted=?,rows_rejected=?,changed=?,issues=? WHERE id=?',
                  (status,fetched_at,str(rel),hashlib.sha256(raw_bytes).hexdigest(),len(rows),len(accepted),rejected,changed,dumps(issues),batch))
        # Not a coverage guarantee. Failed/partial batches never advance the successful cursor.
        if accepted and not rejected:
            latest=accepted[-1]['event_time']
            c.execute('INSERT INTO watermarks VALUES(?,?,?,?) ON CONFLICT(symbol,interval) DO UPDATE SET event_time=excluded.event_time,batch_id=excluded.batch_id WHERE excluded.event_time>=watermarks.event_time',
                      (spec['symbol'],spec['interval'],latest,batch))
    return dict(batch_id=batch,symbol=spec['symbol'],interval=spec['interval'],status=status,
                rows=len(rows),accepted=len(accepted),rejected=rejected,changed=changed,issues=issues)


def observations(symbol, interval, limit=2000, c=None):
    sql = (f'SELECT b.{bars.COLUMNS.replace(",", ",b.")},f.batch_id,f.fetched_at FROM bars b JOIN batch_refs f ON f.id=b.ref '
           'WHERE b.symbol=? AND b.interval=? AND b.ts>=? ORDER BY b.ts DESC LIMIT ?')
    params = (symbol, interval, ts_of(history_start()), limit)
    if c is None:
        with db() as conn:
            rows = conn.execute(sql, params).fetchall()
    else:
        rows = c.execute(sql, params).fetchall()
    return [dict(bars.to_payload(r, SH), batch_id=r['batch_id'], fetched_at=r['fetched_at']) for r in reversed(rows)]


def series_span(c, symbol, interval):
    r=c.execute('SELECT min(ts) s,max(ts) e,count(*) n FROM bars WHERE symbol=? AND interval=? AND ts>=?',(symbol,interval,ts_of(history_start()))).fetchone()
    return dict(start=event_time(r['s']) if r['n'] else None,end=event_time(r['e']) if r['n'] else None,count=r['n'])


_CALENDAR_CACHE = {'key': None, 'dates': [], 'set': set()}


def _load_calendar(c=None):
    global _CALENDAR_CACHE
    if c is None:
        with db() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='calendar'").fetchone()
    else:
        row = c.execute("SELECT value FROM metadata WHERE key='calendar'").fetchone()
    raw = row['value'] if row else None
    key = (str(DATA), raw)
    cached = _CALENDAR_CACHE
    if cached['key'] == key:
        return cached
    dates = json.loads(raw)['dates'] if raw else []
    loaded = {'key': key, 'dates': dates, 'set': set(dates)}
    _CALENDAR_CACHE = loaded
    return loaded


def calendar_state(at=None, c=None):
    at = (at or now()).astimezone(SH)
    day = at.date().isoformat()
    cal = _load_calendar(c)
    days = cal['dates']
    if not days or not days[0] <= day <= days[-1]:
        return dict(state='unknown_calendar', label='日历未覆盖', date=day)
    idx = bisect.bisect_left(days, day)
    prior_day = days[idx - 1] if idx > 0 else None
    result = dict(date=day, previous_trade_date=prior_day, calendar_source='新浪 / AKShare')
    if day not in cal['set']:
        return dict(result, state='closed', label='休市', expected_daily=prior_day)
    minute = at.hour * 60 + at.minute
    state = 'trading' if 570 <= minute < 690 or 780 <= minute < 900 else 'lunch' if 690 <= minute < 780 else 'preopen' if minute < 570 else 'closed'
    return dict(result, state=state, label={'trading': '交易时段', 'lunch': '午间休市', 'preopen': '盘前', 'closed': '已收盘'}[state],
                expected_daily=day if minute >= 1050 else prior_day)


def freshness(record, interval, at=None, c=None):
    at = at or now()
    market = calendar_state(at, c=c)
    if not record:
        return dict(state='missing', label='尚无数据', market=market)
    event = datetime.fromisoformat(record['event_time'])
    local = at.astimezone(SH)
    if interval == '1d':
        expected = market.get('expected_daily')
        state = 'historical' if expected and event.date().isoformat() >= expected else 'stale' if expected else 'unknown'
    else:
        end = datetime.combine(local.date(), datetime.min.time(), SH).replace(hour=11, minute=30) if market['state'] == 'lunch' else at
        if market['state'] == 'closed':
            target = market['date'] if market['date'] in calendar_dates(c=c) else market.get('previous_trade_date')
            end = datetime.fromisoformat(target + 'T15:00:00+08:00') if target else at
        elif market['state'] == 'preopen':
            target = market.get('previous_trade_date')
            end = datetime.fromisoformat(target + 'T15:00:00+08:00') if target else at
        lag = (end - event).total_seconds()
        state = 'unknown' if market['state'] == 'unknown_calendar' else 'stale' if lag > (420 if interval == '5m' else 180) else 'recent' if market['state'] == 'trading' else 'closed'
    return dict(
        state=state,
        label={'missing': '尚无数据', 'historical': '历史日线', 'stale': '数据滞后', 'unknown': '时效待核验', 'recent': '近期观测', 'closed': '休市快照'}[state],
        market=market,
        event_time=record['event_time'],
        fetched_at=record['fetched_at'],
    )


def calendar_dates(c=None):
    return list(_load_calendar(c)['dates'])
