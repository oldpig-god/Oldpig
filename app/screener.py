"""Stock screen: defines the only A-shares the workbench analyses.

Rules, evaluated in this order (the funnel shows how many stocks survive each step):
  1. not_st     名称不含 ST / *ST，且非退市整理
  2. cap        总市值 > 500 亿元（批量快照 f20）
  3. pe_positive 市盈率TTM > 0（批量快照 f115）
  4. pe_band    （可关闭）市盈率TTM 位于所属行业均值 ± 2 个标准差内。
                行业 = 东方财富行业（快照 f100）；样本 = 同行业、非ST、PE TTM>0 的当前A股；
                样本少于 MIN_PEERS 时无法判断，记为 None 且不剔除。
  5. ma         MA5 > MA10 或 MA5 > MA20
  6. ema        EMA12 > EMA20 或 EMA12 > EMA50
  7. macd       MACD 快线 DIF(12,26) > 慢线 DEA(9)

Technical rules use the latest *closed* daily bar of a 前复权 (qfq) series fetched for the
candidates only, so ex-dividend gaps do not distort the averages. Indicators skip suspended
sessions (session-continuous), identical to the chart API. Raw responses are kept as evidence.
A candidate whose latest bar is older than the run's as_of (suspended) or with fewer than MIN_BARS
bars is not judged (checks = None, never a pass). EMA50 needs >= MIN_BARS bars after listing; for very
new listings its SMA-seeded value differs from long-history software values.
Run: python -m app.screener [--no-spot] [--no-band]
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from . import core, collector, history_source, indicators, market, process_lock, tencent_source

MIN_CAP = 5e10           # 500 亿元
BAND_SIGMA = 2.0
MIN_PEERS = 5
LOOKBACK_DAYS = 420      # ~280 sessions: enough warm-up for EMA50 / MACD(12,26,9)
MIN_BARS = 60
WORKERS = max(1, min(int(os.environ.get('FINANCE_SCREEN_WORKERS', '3')), 4))
DELAY = .25
RETRIES = 2              # extra attempts per candidate on network errors
RETRY_DELAY = 2.0
STEPS = [
    ('universe', '当前A股'),
    ('not_st', '剔除ST'),
    ('cap', '总市值>500亿'),
    ('pe_positive', '市盈率TTM>0'),
    ('pe_band', '行业PE均值±2σ'),
    ('ma', 'MA5>MA10或MA20'),
    ('ema', 'EMA12>EMA20或EMA50'),
    ('macd', 'MACD DIF>DEA'),
]
RULES = [k for k, _ in STEPS[1:]]
ST_PATTERN = re.compile(r'ST', re.I)


def is_st(name):
    name = (name or '').strip()
    return bool(ST_PATTERN.search(name)) or name.startswith('退市') or name.endswith('退')


def _float(v, digits=4):
    if v is None:
        return None
    f = float(v)
    return round(f, digits) if np.isfinite(f) else None


# ───────── fundamentals ─────────

def load_stocks(c):
    """Active A-shares with their latest snapshot payload."""
    rows = c.execute("SELECT i.symbol,i.code,i.exchange,i.name,q.payload FROM instruments i LEFT JOIN market_quotes q USING(symbol) "
                     "WHERE i.active=1 AND i.kind='stock' ORDER BY i.symbol").fetchall()
    out = []
    for r in rows:
        q = json.loads(r['payload']) if r['payload'] else {}
        out.append(dict(symbol=r['symbol'], code=r['code'], exchange=r['exchange'], name=r['name'],
                        close=q.get('close'), pct_change=q.get('pct_change_source'), amount=q.get('amount_raw'),
                        market_cap=q.get('market_cap'), float_cap=q.get('float_cap'), pe_ttm=q.get('pe_ttm'),
                        industry=q.get('industry'), quote_time=q.get('event_time') or q.get('fetched_at'),
                        quote_fetched_at=q.get('fetched_at')))
    return out


def industry_stats(stocks):
    """Mean / sample std of PE TTM per industry over non-ST peers with PE TTM > 0."""
    groups = {}
    for s in stocks:
        if s['industry'] and s['pe_ttm'] is not None and s['pe_ttm'] > 0 and not is_st(s['name']):
            groups.setdefault(s['industry'], []).append(s['pe_ttm'])
    stats = {}
    for industry, values in groups.items():
        n = len(values)
        mean = statistics.fmean(values)
        std = statistics.stdev(values) if n > 1 else None
        ok = n >= MIN_PEERS and std is not None
        stats[industry] = dict(industry=industry, n=n, mean=_float(mean), std=_float(std) if std is not None else None,
                               low=_float(mean - BAND_SIGMA * std) if ok else None, high=_float(mean + BAND_SIGMA * std) if ok else None,
                               median=_float(statistics.median(values)))
    return stats


def fundamental_checks(stock, stats):
    pe, cap = stock['pe_ttm'], stock['market_cap']
    checks = {'not_st': not is_st(stock['name']),
              'cap': None if cap is None else cap > MIN_CAP,
              'pe_positive': None if pe is None else pe > 0}
    band = stats.get(stock['industry']) if stock['industry'] else None
    if pe is None or not band or band['low'] is None:
        checks['pe_band'] = None
    else:
        checks['pe_band'] = band['low'] <= pe <= band['high']
    return checks, band


# ───────── technicals ─────────

def technicals(rows):
    """Latest MA / EMA / MACD values on valid closes. Returns None when history is too short."""
    rows = [r for r in rows if r.get('close') is not None]
    if len(rows) < MIN_BARS:
        return None
    close = pd.Series([float(r['close']) for r in rows], dtype=float)
    ma = {n: close.rolling(n, min_periods=n).mean().iloc[-1] for n in (5, 10, 20)}
    ema = {n: indicators.smooth(close, n, 2 / (n + 1)).iloc[-1] for n in (12, 20, 50)}
    dif, dea, hist = (s.iloc[-1] for s in indicators._macd_components(close))
    values = dict(ma5=ma[5], ma10=ma[10], ma20=ma[20], ema12=ema[12], ema20=ema[20], ema50=ema[50],
                  dif=dif, dea=dea, macd_hist=hist)
    values = {k: _float(v) for k, v in values.items()}
    values.update(bar_date=rows[-1]['event_time'][:10], close_qfq=_float(rows[-1]['close']), bars=len(rows))
    return values


def technical_checks(t):
    if not t or any(t[k] is None for k in ('ma5', 'ma10', 'ma20', 'ema12', 'ema20', 'ema50', 'dif', 'dea')):
        return {'ma': None, 'ema': None, 'macd': None}
    return {'ma': t['ma5'] > t['ma10'] or t['ma5'] > t['ma20'],
            'ema': t['ema12'] > t['ema20'] or t['ema12'] > t['ema50'],
            'macd': t['dif'] > t['dea']}


def passes(checks, industry_band=True):
    for rule in RULES:
        if rule == 'pe_band':
            if industry_band and checks.get(rule) is False:
                return False
        elif checks.get(rule) is not True:
            return False
    return True


# ───────── evidence fetch ─────────

def _fetch_tencent_qfq(stock, start_iso, end_iso):
    params = {'param': f"{tencent_source.tx_code(stock['symbol'])},day,{start_iso},{end_iso},420,qfq"}
    resp = tencent_source.get(tencent_source.HISTORY, params)
    values, _ = tencent_source.decode_history(resp.content, stock['symbol'], 1)
    return [dict(zip(['日期', '开盘', '收盘', '最高', '最低', '成交量'], cells[:6])) for cells in values]


def fetch_qfq(stock, fetched_at):
    local = datetime.fromisoformat(fetched_at).astimezone(core.SH)
    start_dt = local - timedelta(days=LOOKBACK_DAYS)
    params = dict(symbol=stock['code'], period='daily', start_date=start_dt.strftime('%Y%m%d'),
                  end_date=local.strftime('%Y%m%d'), adjust='qfq')
    raw = None
    if os.environ.get('FINANCE_TECH_PROVIDER') == 'tencent':
        raw = _fetch_tencent_qfq(stock, start_dt.date().isoformat(), local.date().isoformat())
    else:
        for attempt in range(RETRIES + 1):
            try:
                raw = history_source.fetch(stock, params)
                break
            except requests.RequestException:
                # push2his read timeouts through the proxy are transient; a schema/symbol error is not retried.
                if attempt == RETRIES:
                    raise
                time.sleep(RETRY_DELAY * (attempt + 1))
    accepted = []
    for row in raw:
        value, _ = core.normalize(row, stock['symbol'], '1d', fetched_at)  # drops the unfinished current-day bar
        if value is not None:
            accepted.append(value)
    accepted.sort(key=lambda r: r['event_time'])
    return params, raw, accepted


def refresh_spot(warnings):
    """Whole-market stock snapshot: provides price, market cap, PE TTM and industry."""
    with core.db() as c:
        r = c.execute("SELECT value FROM metadata WHERE key='market_inventory'").fetchone()
    today = core.now().astimezone(core.SH).date().isoformat()
    if importlib.util.find_spec('akshare') and (not r or datetime.fromisoformat(json.loads(r['value'])['fetched_at']).astimezone(core.SH).date().isoformat() != today):
        try:
            result = market.refresh(inventory=True)[0]
            if result['status'] == 'error':
                warnings.append('A股名录更新失败，沿用旧名录：' + str(result.get('error'))[:160])
        except Exception as exc:
            warnings.append('A股名录更新失败，沿用旧名录：' + str(exc)[:160])
    rows, adapter = market.fetch('stock_zh_a_spot_em')
    result = market.ingest(rows, 'stock', 'stock_zh_a_spot_em', adapter=adapter)
    if adapter != market.spot_source.VERSION:
        warnings.append('直连快照失败，已用AKShare回退：该来源不含PE TTM / 行业字段，沿用上次直连快照的PE TTM与行业')
    return result


def update_chart_bars(symbol):
    """Incremental unadjusted daily update so the chart/quality views of selected stocks stay current."""
    endpoint, params = collector.parameters(symbol, '1d')
    batch = core.begin_batch(symbol, '1d', endpoint + ' / direct-kline-v1', params, 'screen')
    try:
        return core.publish(batch, history_source.fetch(core.instrument(symbol), params))
    except Exception as exc:
        core.fail_batch(batch, str(exc))
        return dict(symbol=symbol, status='error', error=str(exc)[:300])


# ───────── run ─────────

def run(refresh=True, progress=None, update_bars=True):
    core.init()
    with (core.DATA / 'screen.lock').open('a+b') as lock:
        try:
            process_lock.acquire(lock)
        except BlockingIOError:
            return {'status': 'busy'}
        run_id = uuid.uuid4().hex
        started = core.iso()
        warnings = []
        spot = None
        if refresh:
            try:
                spot = refresh_spot(warnings)
            except Exception as exc:
                warnings.append('批量快照更新失败，使用已保存快照：' + str(exc)[:200])
        with core.db() as c:
            stocks = load_stocks(c)
        if not stocks:
            raise RuntimeError('没有当前A股名录，请先运行 python -m app.market --inventory')
        if sum(s['pe_ttm'] is not None for s in stocks) < len(stocks) * .5:
            raise RuntimeError('已保存快照缺少市盈率TTM / 行业字段，请联网后重新运行筛选（需要直连快照）')
        stats = industry_stats(stocks)
        candidates = []
        for s in stocks:
            checks, band = fundamental_checks(s, stats)
            if checks['not_st'] and checks['cap']:
                candidates.append(dict(s, checks=checks, band=band))
        total = len(candidates)
        fetched_at = core.iso()
        evidence, done = {}, [0]
        guard = threading.Lock()

        def work(item, stage='technicals'):
            item['network_error'] = False
            try:
                params, raw, rows = fetch_qfq(item, fetched_at)
                item['technicals'] = technicals(rows)
                # Empty / too-short history cannot be judged: count it as a fetch problem, never a silent reject.
                item['fetch_error'] = None if item['technicals'] else f'日线不足{MIN_BARS}根（{len(rows)}根），无法判断'
            except Exception as exc:
                params, raw = None, None
                item['technicals'] = None
                item['fetch_error'] = str(exc)[:300]
                item['network_error'] = isinstance(exc, requests.RequestException)
            item['checks'].update(technical_checks(item['technicals']))
            with guard:
                evidence[item['symbol']] = dict(parameters=params, rows=raw, error=item['fetch_error'])
                done[0] += 1
                if progress:
                    progress(dict(stage=stage, done=done[0], total=total, symbol=item['symbol']))
            time.sleep(DELAY)

        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(work, candidates))
        # Final polite pass: network failures once more, one at a time, after a pause.
        retry = [cand for cand in candidates if cand.pop('network_error', False)]
        if retry:
            time.sleep(RETRY_DELAY * 2)
            done[0], total = 0, len(retry)
            for cand in retry:
                work(cand, 'retry')
        for cand in candidates:
            cand.pop('network_error', None)
        total = len(candidates)

        # Raw evidence, one file per run (same layout as other batches).
        batch = core.begin_batch('__SCREEN__', 'screen', 'stock_zh_a_hist qfq / direct-kline-v1', {'run_id': run_id, 'lookback_days': LOOKBACK_DAYS}, 'screen-v1')
        raw = dict(batch_id=batch, run_id=run_id, endpoint='push2his kline fqt=1', fetched_at=fetched_at, series=evidence)
        data = gzip.compress(core.dumps(raw).encode(), compresslevel=3, mtime=0)
        rel = core.Path('raw') / fetched_at[:10] / f'{batch}.json.gz'
        path = core.DATA / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        errors = sum(1 for c in candidates if c['fetch_error'])
        with core.db() as c:
            c.execute('UPDATE batches SET status=?,finished_at=?,raw_path=?,raw_sha256=?,rows_in=?,rows_accepted=?,rows_rejected=?,issues=? WHERE id=?',
                      ('ok' if not errors else 'partial', core.iso(), str(rel), hashlib.sha256(data).hexdigest(), total, total - errors, errors,
                       core.dumps({'fetch_errors': errors}), batch))

        items = []
        for cand in candidates:
            items.append(dict(symbol=cand['symbol'], code=cand['code'], exchange=cand['exchange'], name=cand['name'],
                              industry=cand['industry'], close=cand['close'], pct_change=cand['pct_change'], amount=cand['amount'],
                              market_cap=cand['market_cap'], float_cap=cand['float_cap'], pe_ttm=cand['pe_ttm'],
                              quote_time=cand['quote_time'], band=cand['band'], technicals=cand['technicals'],
                              checks=cand['checks'], fetch_error=cand['fetch_error']))
        items.sort(key=lambda x: -(x['market_cap'] or 0))
        bar_dates = [i['technicals']['bar_date'] for i in items if i['technicals']]
        as_of = max(bar_dates) if bar_dates else None
        for i in items:
            i['stale'] = bool(i['technicals'] and as_of and i['technicals']['bar_date'] < as_of)
            if i['stale']:
                # Latest closed bar is older than the market's (e.g. suspended): frozen indicators are not judged.
                i['checks'].update(ma=None, ema=None, macd=None)
        counts = dict(universe=len(stocks), not_st=sum(1 for s in stocks if not is_st(s['name'])))
        quote_times = [s['quote_fetched_at'] for s in stocks if s['quote_fetched_at']]
        result = dict(id=run_id, created_at=core.iso(), started_at=started, as_of=as_of, status='ok' if not errors else 'partial',
                      quote_fetched_at=max(quote_times) if quote_times else None, spot=spot, warnings=warnings,
                      criteria=dict(min_cap=MIN_CAP, band_sigma=BAND_SIGMA, min_peers=MIN_PEERS, adjustment='qfq', lookback_days=LOOKBACK_DAYS),
                      counts=counts, batch_id=batch, fetch_errors=errors, items=items,
                      industries={k: v for k, v in stats.items() if any(i['industry'] == k for i in items)})
        with core.db() as c:
            c.execute('INSERT INTO screen_runs(id,created_at,as_of,status,payload) VALUES(?,?,?,?,?)',
                      (run_id, result['created_at'], as_of, result['status'], core.dumps(result)))
        if update_bars:
            selected = [i['symbol'] for i in items if passes(i['checks'], True)]
            for n, symbol in enumerate(selected, 1):
                update_chart_bars(symbol)
                if progress:
                    progress(dict(stage='chart_bars', done=n, total=len(selected), symbol=symbol))
                time.sleep(DELAY)
        return dict(status=result['status'], id=run_id, as_of=as_of, candidates=total, fetch_errors=errors,
                    passed=sum(passes(i['checks']) for i in items), warnings=warnings)


# ───────── read ─────────

def latest_payload(c=None):
    def q(conn):
        return conn.execute('SELECT payload FROM screen_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if c is None:
        with core.db() as conn:
            row = q(conn)
    else:
        row = q(c)
    return json.loads(row['payload']) if row else None


def view(payload, industry_band=True):
    """API shape: every candidate with its checks, the pass flag under the chosen rule set, and the funnel."""
    if not payload:
        return dict(run=None, items=[], funnel=[], industry_band=industry_band)
    items = []
    for item in payload['items']:
        items.append(dict(item, passed=passes(item['checks'], industry_band)))
    alive = list(payload['items'])
    funnel = [dict(key='universe', label=STEPS[0][1], count=payload['counts']['universe']),
              dict(key='not_st', label=STEPS[1][1], count=payload['counts']['not_st']),
              dict(key='cap', label=STEPS[2][1], count=len(alive))]
    for key, label in STEPS[3:]:
        if key == 'pe_band':
            if not industry_band:
                funnel.append(dict(key=key, label=label, count=None, skipped=True))
                continue
            alive = [i for i in alive if i['checks'].get(key) is not False]
        else:
            alive = [i for i in alive if i['checks'].get(key) is True]
        funnel.append(dict(key=key, label=label, count=len(alive)))
    run_meta = {k: payload.get(k) for k in ('id', 'created_at', 'as_of', 'status', 'quote_fetched_at', 'criteria', 'warnings', 'fetch_errors', 'batch_id')}
    return dict(run=run_meta, items=items, funnel=funnel, industry_band=industry_band,
                passed=sum(i['passed'] for i in items), industries=payload.get('industries', {}),
                rules=[dict(key=k, label=l) for k, l in STEPS[1:]])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='按当前策略筛选A股')
    p.add_argument('--no-spot', action='store_true', help='不重新抓取批量快照，使用已保存快照')
    p.add_argument('--no-bars', action='store_true', help='不更新入选股票的不复权日线')
    p.add_argument('--no-band', action='store_true', help='打印结果时不使用行业PE±2σ条件')
    a = p.parse_args()
    out = run(refresh=not a.no_spot, update_bars=not a.no_bars,
              progress=lambda r: print(json.dumps(r, ensure_ascii=False), flush=True) if r['done'] % 25 == 0 or r['done'] == r['total'] else None)
    print(json.dumps(out, ensure_ascii=False))
    if out.get('id'):
        v = view(latest_payload(), not a.no_band)
        print(' → '.join(f"{f['label']} {f['count'] if f['count'] is not None else '跳过'}" for f in v['funnel']))
        for i in v['items']:
            if i['passed']:
                print(f"{i['symbol']} {i['name']} {i['industry']} 市值{i['market_cap'] / 1e8:.0f}亿 PE {i['pe_ttm']}")
