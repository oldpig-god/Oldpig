"""Direct whole-market snapshot adapter: the same Eastmoney clist endpoints and fields as AKShare's
stock_zh_a_spot_em / fund_etf_spot_em, fetched in-process (no SDK subprocess / pandas import) with a
shorter, bounded inter-page delay than AKShare's 0.5-1.5 s. Rows keep AKShare's Chinese column names so
ingest(), raw evidence files and the UI stay unchanged.

Rate limits: the source resets connections when requested too fast (observed with 3 parallel workers,
~8 req/s). Defaults are sequential, ~2 req/s, and the adapter stops at the first connection reset
instead of retrying, so a block is not prolonged. Tunable via FINANCE_SPOT_WORKERS / FINANCE_SPOT_DELAY.
"""
import math
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

VERSION = 'direct-clist-v1'
UT = 'bd1d9ddb04089700cf9c27f6f7426281'
SPECS = {
    # Stock rows also carry the screening fundamentals: total market value (CNY), PE TTM and the
    # Eastmoney industry. Hosts are tried in order; a host that refuses page 1 falls through to the next.
    'stock_zh_a_spot_em': dict(
        urls=['https://push2delay.eastmoney.com/api/qt/clist/get', 'https://82.push2.eastmoney.com/api/qt/clist/get'],
        params={'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048'},
        fields={'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f3': '涨跌幅', 'f4': '涨跌额', 'f5': '成交量', 'f6': '成交额',
                'f7': '振幅', 'f15': '最高', 'f16': '最低', 'f17': '今开', 'f18': '昨收', 'f8': '换手率', 'f10': '量比',
                'f20': '总市值', 'f21': '流通市值', 'f9': '市盈率-动态', 'f115': '市盈率TTM', 'f100': '行业', 'f124': '更新时间'}),
    'fund_etf_spot_em': dict(
        urls=['https://push2delay.eastmoney.com/api/qt/clist/get'],
        params={'fs': 'b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827', 'wbp2u': '|0|0|0|web'},
        fields={'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f3': '涨跌幅', 'f4': '涨跌额', 'f5': '成交量', 'f6': '成交额',
                'f7': '振幅', 'f17': '开盘价', 'f15': '最高价', 'f16': '最低价', 'f18': '昨收', 'f8': '换手率', 'f10': '量比',
                'f441': 'IOPV实时估值', 'f402': '基金折价率', 'f297': '数据日期', 'f124': '更新时间'}),
}
PAGE_SIZE = 100
WORKERS = max(1, min(int(os.environ.get('FINANCE_SPOT_WORKERS', '1')), 3))
DELAY = max(.2, float(os.environ.get('FINANCE_SPOT_DELAY', '0.45')))


class SourceBlocked(RuntimeError):
    """Connection refused/reset by the source: retrying (or falling back to the SDK) would hit the same host."""


def _value(key, raw):
    if raw in (None, '-', ''):
        return None
    if key == 'f124':  # unix seconds -> same ISO-UTC text AKShare's DataFrame.to_json produced
        try:
            return datetime.fromtimestamp(int(raw), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        except (TypeError, ValueError, OverflowError):
            return None
    if key in ('f12', 'f14', 'f297', 'f100'):
        return str(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _page(session, spec, page, url):
    params = dict(pn=str(page), pz=str(PAGE_SIZE), po='1', np='1', ut=UT, fltt='2', invt='2', fid='f12',
                  fields=','.join(spec['fields']), **spec['params'])
    last = None
    for attempt in range(2):
        try:
            if page > 1 or attempt:
                time.sleep(DELAY * random.uniform(.7, 1.3) + attempt * 2)
            r = session.get(url, params=params, timeout=(5, 15))
            r.raise_for_status()
            payload = r.json()
            data = payload.get('data')
            if payload.get('rc', 0) != 0 or not data or 'diff' not in data:
                raise ValueError(f'Source response error on page {page}: rc={payload.get("rc")}')
            diff = data['diff']
            return int(data['total']), list(diff.values()) if isinstance(diff, dict) else diff
        except requests.ConnectionError as exc:
            raise SourceBlocked(f'page {page}: connection refused/reset by source ({exc.__class__.__name__}); stopping') from exc
        except (requests.RequestException, ValueError) as exc:
            last = exc
    raise RuntimeError(f'page {page} failed: {last}')


def fetch(endpoint):
    spec = SPECS[endpoint]
    with requests.Session() as session:
        session.mount('https://', requests.adapters.HTTPAdapter(pool_maxsize=WORKERS))
        blocked = None
        for url in spec['urls']:
            try:
                total, first = _page(session, spec, 1, url)
                break
            except SourceBlocked as exc:
                blocked = exc  # host unreachable/refusing: try the next documented host once
        else:
            raise blocked
        pages = math.ceil(total / PAGE_SIZE) if total else 0
        items = list(first)
        if pages > 1 and WORKERS > 1:
            with ThreadPoolExecutor(WORKERS) as pool:
                for _, rows in pool.map(lambda p: _page(session, spec, p, url), range(2, pages + 1)):
                    items.extend(rows)
        else:
            for p in range(2, pages + 1):
                items.extend(_page(session, spec, p, url)[1])
    rows, seen = [], set()
    for item in items:
        row = {name: _value(key, item.get(key)) for key, name in spec['fields'].items()}
        code = row['代码']
        if not code or code in seen:
            continue  # pagination overlap while the list shifts; ingest() also quarantines conflicts
        seen.add(code)
        rows.append(row)
    if not rows or len(rows) < total * .98:
        raise ValueError(f'Incomplete market response: {len(rows)} of {total}')
    return rows
