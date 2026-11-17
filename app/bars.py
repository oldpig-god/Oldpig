"""Columnar price store codec.

Legacy storage kept every bar as a zlib-compressed canonical-JSON payload (~300 B) plus text keys,
hashes and batch ids (~160 B). Most of that JSON is constant per interval (currency, adjustment,
source, units, quality) or derivable (event_time/timestamp from one epoch value). `bars` keeps only
the varying numbers as REAL columns; to_payload() rebuilds the exact legacy dict, so the API,
normalisation and audit semantics do not change. Anything that does not round-trip is kept verbatim
in `extra` (JSON), so the conversion is lossless by construction.
"""
import hashlib
import json
from datetime import datetime

SCHEMA = '''
CREATE TABLE IF NOT EXISTS bars (
  symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL, amount REAL, pct REAL,
  flags INTEGER NOT NULL DEFAULT 0, hash INTEGER NOT NULL, ref INTEGER NOT NULL, extra TEXT,
  PRIMARY KEY(symbol, interval, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS batch_refs (
  id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, fetched_at TEXT NOT NULL, UNIQUE(batch_id, fetched_at));
'''
COLUMNS = 'symbol,interval,ts,open,high,low,close,volume,amount,pct,flags,hash,ref,extra'
FLAG_BITS = {'open_absent_in_trend_payload': 1}
SOURCE = '东方财富 / AKShare'
_ABSENT = '__absent__'


def digest(canonical):
    """Change-detection hash: first 64 bits of the legacy sha256(canonical JSON), as signed int."""
    return int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:8], 'big', signed=True)


def hash_from_hex(hexdigest):
    return int.from_bytes(bytes.fromhex(hexdigest)[:8], 'big', signed=True)


def _base(symbol, interval, ts, o, h, l, c, v, a, p, flags, tz):
    names = [n for n, bit in FLAG_BITS.items() if flags & bit]
    d = dict(symbol=symbol, interval=interval, event_time=datetime.fromtimestamp(ts, tz).isoformat(),
             timestamp=ts * 1000, close=c, currency='CNY', adjustment='raw', volume_raw=v,
             volume_unit='source_unverified', amount_raw=a, source=SOURCE, pct_change_source=p,
             quality='price_only' if interval == '1m' else 'valid_ohlc', flags=names)
    if interval != '1m':
        d.update(open=o, high=h, low=l)
    return d


def to_payload(row, tz):
    """row: sequence/Row with COLUMNS order (symbol..extra) -> exact legacy payload dict."""
    d = _base(*row[:11], tz)
    extra = row[13]
    if extra:
        e = json.loads(extra)
        for k in e.pop(_ABSENT, []):
            d.pop(k, None)
        d.update(e)
    return d


def from_payload(p, tz, ref):
    """Legacy payload dict -> bars tuple (lossless: residual differences go to `extra`)."""
    ts = p['timestamp'] // 1000 if isinstance(p.get('timestamp'), int) and p['timestamp'] % 1000 == 0 \
        else int(datetime.fromisoformat(p['event_time']).timestamp())
    flags = 0
    for name in p.get('flags') or []:
        flags |= FLAG_BITS.get(name, 0)
    num = lambda k: p.get(k) if isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool) else None
    values = (p['symbol'], p['interval'], ts, num('open'), num('high'), num('low'), num('close'),
              num('volume_raw'), num('amount_raw'), num('pct_change_source'), flags)
    rebuilt = _base(*values, tz)
    extra = {k: v for k, v in p.items() if k not in rebuilt or rebuilt[k] != v or type(rebuilt[k]) is not type(v)}
    missing = [k for k in rebuilt if k not in p]
    if missing:
        extra[_ABSENT] = missing
    canonical = json.dumps(p, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))
    close = values[6] if values[6] is not None else 0.0
    return values[:6] + (close,) + values[7:] + (digest(canonical), ref,
                                                   json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None)
