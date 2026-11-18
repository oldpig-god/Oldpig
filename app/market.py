"""Whole-market inventory and quotes. Snapshots are never fabricated minute bars."""
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from . import core, spot_source

ENDPOINTS={'stock':'stock_zh_a_spot_em','etf':'fund_etf_spot_em'}

def exchange(code,kind):
    # Includes BSE's new 920 codes as well as its legacy 4/8 codes.
    if not re.fullmatch(r'\d{6}',code):raise ValueError('Invalid six-digit code')
    if kind=='etf':
        if code.startswith('5'):return 'SH'
        if code.startswith('1'):return 'SZ'
    else:
        if code.startswith(('4','8','920')):return 'BJ'
        if code.startswith('6'):return 'SH'
        if code.startswith(('0','3')):return 'SZ'
    raise ValueError(f'Unrecognized {kind} code: {code}')


def fetch_sdk(endpoint):
    r=subprocess.run([sys.executable,'-m','app.provider'],cwd=core.ROOT,input=json.dumps({'endpoint':endpoint,'parameters':{}}),capture_output=True,text=True,encoding='utf-8',env=dict(core.os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1'),timeout=180)
    if r.returncode:raise RuntimeError(r.stderr[-700:])
    rows=json.loads(r.stdout)
    if not isinstance(rows,list) or not rows:raise ValueError('Empty market response')
    return rows


def fetch(endpoint):
    """Returns (rows, adapter). Snapshots use the concurrent direct adapter (~10 s for the whole
    market instead of >1 min); the AKShare SDK process remains the fallback and the inventory source."""
    if endpoint in spot_source.SPECS:
        try:return spot_source.fetch(endpoint),spot_source.VERSION
        except spot_source.SourceBlocked:raise  # SDK hits the same host; don't prolong a rate-limit block
        except Exception as exc:
            print(json.dumps({'endpoint':endpoint,'direct_adapter_failed':str(exc)[:300]},ensure_ascii=False),flush=True)
    return fetch_sdk(endpoint),'akshare-sdk'


def _sdk_version():
    try:return importlib.metadata.version('akshare')
    except importlib.metadata.PackageNotFoundError:return 'none'


def ingest(rows,kind,endpoint,fetched_at=None,inventory_only=False,adapter=None):
    core.init();fetched_at=fetched_at or core.iso()
    batch=core.begin_batch('__'+kind.upper()+'__','inventory' if inventory_only else 'spot',endpoint,{'adapter':adapter} if adapter else {},_sdk_version())
    raw={'batch_id':batch,'endpoint':endpoint,'adapter':adapter,'fetched_at':fetched_at,'rows':rows}
    data=core.dumps(raw).encode();rel=core.Path('raw')/fetched_at[:10]/(batch+'.json');path=core.DATA/rel;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    items={};rejected=[];conflicts=set()
    for r in rows:
        try:
            code=str(r.get('code') if inventory_only else r.get('代码')).zfill(6)
            name=str(r.get('name') if inventory_only else r.get('名称')).strip()
            ex=exchange(code,kind);symbol=code+'.'+ex
            if not name or name=='None':raise ValueError('Missing name')
            if symbol in items and items[symbol][1]!=r:conflicts.add(symbol)
            items[symbol]=({'symbol':symbol,'code':code,'exchange':ex,'name':name,'kind':kind},r)
        except Exception as e:rejected.append({'row':r,'error':str(e)})
    for symbol in conflicts:items.pop(symbol,None)
    with core.db() as c:
        for symbol,(item,r) in items.items():
            c.execute('INSERT INTO instruments VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,source=excluded.source,seen_at=excluded.seen_at,active=1',
                      (symbol,item['code'],item['exchange'],item['name'],kind,endpoint,fetched_at))
            if not inventory_only:
                event_time=None
                try:
                    dt=datetime.fromisoformat(str(r.get('更新时间')).replace('Z','+00:00'))
                    if dt.tzinfo and dt<=datetime.fromisoformat(fetched_at):event_time=dt.astimezone(core.SH).isoformat()
                except (ValueError,TypeError):pass
                value={'symbol':symbol,'close':core.numeric(r.get('最新价'),positive=True),'pct_change_source':core.numeric(r.get('涨跌幅')),
                       'event_time':event_time,'fetched_at':fetched_at,'volume_raw':core.numeric(r.get('成交量'),nonnegative=True),
                       'amount_raw':core.numeric(r.get('成交额'),nonnegative=True),'source':'东方财富 / AKShare','quality':'snapshot',
                       'time_semantics':'source_update' if event_time else 'fetch_time_only'}
                if kind=='stock':
                    # Screening fundamentals; None when the source omits them.
                    industry=r.get('行业')
                    value.update(market_cap=core.numeric(r.get('总市值'),positive=True),float_cap=core.numeric(r.get('流通市值'),positive=True),
                                 pe_ttm=core.numeric(r.get('市盈率TTM')),pe_dynamic=core.numeric(r.get('市盈率-动态')),
                                 industry=str(industry).strip() if industry not in (None,'','-') else None)
                    if '市盈率TTM' not in r:
                        # SDK fallback has no PE TTM / industry columns: keep the last direct values (marked with
                        # their own fetch time) instead of wiping the fundamentals the screener depends on.
                        old=c.execute('SELECT payload FROM market_quotes WHERE symbol=?',(symbol,)).fetchone()
                        old=json.loads(old[0]) if old else {}
                        if old.get('pe_ttm') is not None or old.get('industry'):
                            value.update(pe_ttm=old.get('pe_ttm'),industry=old.get('industry'),
                                         fundamentals_fetched_at=old.get('fundamentals_fetched_at') or old.get('fetched_at'))
                c.execute('INSERT INTO market_quotes VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,batch_id=excluded.batch_id,fetched_at=excluded.fetched_at',
                          (symbol,core.dumps(value),batch,fetched_at))
        # Stock quote feeds also contain retired/converted securities. The official
        # exchange-derived membership defines the active A-share universe.
        if inventory_only or kind=='etf':
            old=c.execute('SELECT count(*) FROM market_members WHERE source=?',(endpoint,)).fetchone()[0]
            if not rejected and not conflicts and items and (not old or len(items)>=old*.8):
                c.execute('DELETE FROM market_members WHERE source=?',(endpoint,))
                c.executemany('INSERT INTO market_members VALUES(?,?)',[(endpoint,s) for s in items])
            else:
                rejected.append({'error':'Membership not replaced: partial/conflicting or unexpectedly shrunken response'})
        membership='stock_info_a_code_name' if kind=='stock' else 'fund_etf_spot_em'
        if c.execute('SELECT 1 FROM market_members WHERE source=? LIMIT 1',(membership,)).fetchone():
            c.execute('UPDATE instruments SET active=CASE WHEN symbol IN (SELECT symbol FROM market_members WHERE source=?) THEN 1 ELSE 0 END WHERE kind=?',(membership,kind))
        rejected_count=len(rejected)+len(conflicts)
        status='ok' if items and not rejected_count else 'partial' if items else 'quarantined'
        c.execute('UPDATE batches SET status=?,finished_at=?,raw_path=?,raw_sha256=?,rows_in=?,rows_accepted=?,rows_rejected=?,issues=? WHERE id=?',
                  (status,fetched_at,str(rel),hashlib.sha256(data).hexdigest(),len(rows),len(items),rejected_count,core.dumps({'rejected_examples':rejected[:5],'conflicting_symbols':sorted(conflicts)[:5]}),batch))
        c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('market_'+('inventory' if inventory_only else kind),core.dumps({'batch_id':batch,'endpoint':endpoint,'fetched_at':fetched_at,'rows':len(rows),'unique':len(items),'rejected':rejected_count})))
    return {'batch_id':batch,'kind':kind,'rows':len(rows),'unique':len(items),'rejected':rejected_count,'status':status}


def refresh(inventory=False):
    results=[]
    endpoints=[('stock','stock_info_a_code_name',True)] if inventory else list((k,v,False) for k,v in ENDPOINTS.items())
    blocked=None
    for kind,endpoint,master in endpoints:
        try:
            if blocked:raise spot_source.SourceBlocked('skipped after source block: '+blocked)
            rows,adapter=fetch(endpoint)
            results.append(ingest(rows,kind,endpoint,inventory_only=master,adapter=adapter))
        except Exception as exc:
            if isinstance(exc,spot_source.SourceBlocked) and not blocked:blocked=str(exc)[:200]
            b=core.begin_batch('__'+kind.upper()+'__','inventory' if master else 'spot',endpoint,{})
            core.fail_batch(b,str(exc));results.append({'kind':kind,'status':'error','error':str(exc)})
    return results


def coverage():
    with core.db() as c:
        kinds=[dict(r) for r in c.execute('SELECT kind,exchange,count(*) count FROM instruments WHERE active=1 GROUP BY kind,exchange')]
        quotes=c.execute('SELECT count(*) n FROM market_quotes q JOIN instruments i USING(symbol) WHERE i.active=1').fetchone()['n']
        valid=c.execute("SELECT count(*) n FROM market_quotes q JOIN instruments i USING(symbol) WHERE i.active=1 AND json_extract(q.payload,'$.close') IS NOT NULL").fetchone()['n']
        # series_stats is maintained by core.publish(); avoids scanning millions of observations.
        # symbols = series with records inside the rolling window; rows = locally stored records.
        history=[dict(r) for r in c.execute('SELECT s.interval,sum(s.last_time>=?) symbols,sum(s.rows) rows FROM series_stats s JOIN instruments i USING(symbol) WHERE i.active=1 GROUP BY s.interval HAVING symbols>0',(core.history_start(),))]
        tasks=[dict(r) for r in c.execute('SELECT interval,status,count(*) count FROM history_tasks GROUP BY interval,status')]
        sources={r['key']:json.loads(r['value']) for r in c.execute("SELECT * FROM metadata WHERE key LIKE 'market_%'")}
        missing_prices=[dict(r) for r in c.execute("SELECT i.symbol,i.name,i.kind FROM instruments i JOIN market_quotes q USING(symbol) WHERE i.active=1 AND json_extract(q.payload,'$.close') IS NULL ORDER BY i.symbol LIMIT 25")]
        no_history=[dict(r) for r in c.execute("SELECT i.symbol,i.name,i.kind,t.status FROM instruments i LEFT JOIN history_tasks t ON t.symbol=i.symbol AND t.interval='1d' WHERE i.active=1 AND NOT EXISTS (SELECT 1 FROM series_stats s WHERE s.symbol=i.symbol AND s.interval='1d') ORDER BY i.symbol LIMIT 25")]
        inactive=c.execute('SELECT count(*) FROM instruments WHERE active=0').fetchone()[0]
        missing=[dict(r) for r in c.execute('SELECT i.symbol,i.name,i.kind FROM instruments i LEFT JOIN market_quotes q USING(symbol) WHERE i.active=1 AND q.symbol IS NULL ORDER BY i.symbol LIMIT 25')]
    return {'instruments':kinds,'total':sum(r['count'] for r in kinds),'quote_symbols':quotes,'valid_quote_prices':valid,'history':history,'tasks':tasks,'sources':sources,'missing_quote_examples':missing,'missing_price_examples':missing_prices,'missing_history_examples':no_history,'excluded_inactive':inactive,
            'scope':'当前上游可发现的沪深京A股与沪深场内ETF；不含历史退市全集','history_window':core.history_window()}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--inventory',action='store_true');a=p.parse_args()
    core.init();print(json.dumps(refresh(a.inventory),ensure_ascii=False))
