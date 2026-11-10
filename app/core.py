"""Local research data store. Raw responses, versioned prices and explicit quality state."""
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
    con.execute('PRAGMA journal_mode=WAL')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY, symbol TEXT, interval TEXT, endpoint TEXT, parameters TEXT,
          started_at TEXT, finished_at TEXT, status TEXT, raw_path TEXT, raw_sha256 TEXT,
          rows_in INTEGER DEFAULT 0, rows_accepted INTEGER DEFAULT 0, rows_rejected INTEGER DEFAULT 0,
          changed INTEGER DEFAULT 0, error TEXT, issues TEXT, source_version TEXT);
        CREATE TABLE IF NOT EXISTS observations (
          symbol TEXT, interval TEXT, event_time TEXT, payload TEXT, value_hash TEXT,
          batch_id TEXT, fetched_at TEXT, PRIMARY KEY(symbol, interval, event_time));
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

        ''')

        c.executemany('INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?,?,1)',
                      [(r['symbol'],r['code'],r['exchange'],r['name'],r['kind'],'bootstrap',iso()) for r in UNIVERSE.values()])


def universe():
    with db() as c:
        return {r['symbol']:dict(r) for r in c.execute('SELECT * FROM instruments WHERE active=1 ORDER BY symbol')}


def instrument(symbol):
    with db() as c:r=c.execute('SELECT * FROM instruments WHERE symbol=?',(symbol,)).fetchone()
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
    with db() as c:
        for row in accepted:
            canonical=dumps(row);digest=hashlib.sha256(canonical.encode()).hexdigest();payload=b'Z1'+zlib.compress(canonical.encode(),level=3)
            key=(spec['symbol'],spec['interval'],row['event_time'])
            old=c.execute('SELECT value_hash FROM observations WHERE symbol=? AND interval=? AND event_time=?',key).fetchone()
            if old is None or old['value_hash']!=digest:
                changed+=1
                c.execute('INSERT INTO revisions(symbol,interval,event_time,payload,value_hash,batch_id,fetched_at) VALUES(?,?,?,?,?,?,?)',key+(payload,digest,batch,fetched_at))
            c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,interval,event_time) DO UPDATE SET payload=excluded.payload,value_hash=excluded.value_hash,batch_id=excluded.batch_id,fetched_at=excluded.fetched_at',key+(payload,digest,batch,fetched_at))
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


def observations(symbol, interval, limit=2000):
    with db() as c:
        rows=c.execute('SELECT * FROM observations WHERE symbol=? AND interval=? AND event_time>=? ORDER BY event_time DESC LIMIT ?',
                       (symbol,interval,history_start(),limit)).fetchall()
    return [dict(unpack_payload(r['payload']),batch_id=r['batch_id'],fetched_at=r['fetched_at']) for r in reversed(rows)]


def calendar_state(at=None):
    at=(at or now()).astimezone(SH);day=at.date().isoformat()
    with db() as c:
        r=c.execute("SELECT value FROM metadata WHERE key='calendar'").fetchone()
    calendar=json.loads(r['value']) if r else None
    if not calendar or not calendar['dates'][0]<=day<=calendar['dates'][-1]:
        return dict(state='unknown_calendar',label='日历未覆盖',date=day)
    days=calendar['dates'];prior=[d for d in days if d<day]
    result=dict(date=day,previous_trade_date=prior[-1] if prior else None,calendar_source='新浪 / AKShare')
    if day not in days:return dict(result,state='closed',label='休市',expected_daily=prior[-1] if prior else None)
    minute=at.hour*60+at.minute
    state='trading' if 570<=minute<690 or 780<=minute<900 else 'lunch' if 690<=minute<780 else 'preopen' if minute<570 else 'closed'
    return dict(result,state=state,label={'trading':'交易时段','lunch':'午间休市','preopen':'盘前','closed':'已收盘'}[state],
                expected_daily=day if minute>=1050 else (prior[-1] if prior else None))


def freshness(record, interval, at=None):
    at=at or now();market=calendar_state(at)
    if not record:return dict(state='missing',label='尚无数据',market=market)
    event=datetime.fromisoformat(record['event_time']);local=at.astimezone(SH)
    if interval=='1d':
        expected=market.get('expected_daily')
        state='historical' if expected and event.date().isoformat()>=expected else 'stale' if expected else 'unknown'
    else:
        end=datetime.combine(local.date(),datetime.min.time(),SH).replace(hour=11,minute=30) if market['state']=='lunch' else at
        if market['state']=='closed':
            target=market['date'] if market['date'] in calendar_dates() else market.get('previous_trade_date')
            end=datetime.fromisoformat(target+'T15:00:00+08:00') if target else at
        elif market['state']=='preopen':
            target=market.get('previous_trade_date')
            end=datetime.fromisoformat(target+'T15:00:00+08:00') if target else at
        lag=(end-event).total_seconds()
        state='unknown' if market['state']=='unknown_calendar' else 'stale' if lag>(420 if interval=='5m' else 180) else 'recent' if market['state']=='trading' else 'closed'
    return dict(state=state,label={'missing':'尚无数据','historical':'历史日线','stale':'数据滞后','unknown':'时效待核验','recent':'近期观测','closed':'休市快照'}[state],market=market,
                event_time=record['event_time'],fetched_at=record['fetched_at'])


def calendar_dates():
    with db() as c:r=c.execute("SELECT value FROM metadata WHERE key='calendar'").fetchone()
    return json.loads(r['value'])['dates'] if r else []
