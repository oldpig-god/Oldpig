"""Dated status evidence using AKShare's documented upstream contracts.

Keep the scheduler lightweight (stdlib + requests). Bootstrap uses AKShare itself;
its same-day suspension source is refreshed here without importing pandas in a 256MB worker.
"""
import requests
from . import core,data_health

SOURCE='https://akshare.akfamily.xyz/data/stock/stock_issue.html#id3'

def suspension_evidence(rows, day, existing=None):
    evidence=existing or {}
    for row in rows:
        symbol=row.get('SECUCODE')
        if not symbol or not symbol.endswith(('.SH','.SZ','.BJ')):continue
        start=str(row.get('SUSPEND_START_DATE') or '')[:10]
        if not start or start>day:continue
        # Intraday interruptions do not explain a missing daily candle.
        if row.get('SUSPEND_EXPIRE') not in ('连续停牌','停牌一天','全天停牌'):continue
        end=str(row.get('SUSPEND_END_TIME') or day)[:10]
        end=min(end,day)  # Never treat an expected future resumption as fact.
        if end<start:continue
        item=evidence.setdefault(symbol,{})
        interval=dict(start=start,end=end,source='AKShare stock_tfp_em / Eastmoney',queried_date=day)
        intervals=item.setdefault('suspensions',[])
        intervals[:]=[x for x in intervals if x['start']!=start];intervals.append(interval)
        item.update(source=SOURCE,verified_at=core.iso())
    return evidence

def refresh(store,target):
    prefix='system/health/';key=prefix+'status-source/'+target+'.json.gz'
    old=store.get_json(key,optional=True)
    if old and old.get('status')=='ok':return old
    params=dict(sortColumns='SUSPEND_START_DATE',sortTypes='-1',pageSize='500',pageNumber='1',
        reportName='RPT_CUSTOM_SUSPEND_DATA_INTERFACE',columns='ALL',source='WEB',client='WEB',filter=f'(MARKET="全部")(DATETIME=\'{target}\')')
    try:
        response=requests.get('https://datacenter-web.eastmoney.com/api/data/v1/get',params=params,timeout=(5,12));response.raise_for_status()
        payload=response.json();result=payload.get('result')
        if not isinstance(result,dict) or not isinstance(result.get('data'),list):raise ValueError('Suspension source schema mismatch / empty result')
        rows=result['data']
        # Do not silently accept truncation; normal daily result is much smaller than 500.
        if result.get('pages',1)>1:raise ValueError('Suspension source requires additional pages')
        current=store.get_json(prefix+'security-status.json.gz',optional=True) or {}
        current=suspension_evidence(rows,target,current)
        store.put_json(prefix+'security-status.json.gz',current)
        outcome=dict(status='ok',queried_date=target,fetched_at=core.iso(),source=SOURCE,rows=rows)
    except Exception as exc:outcome=dict(status='failed',queried_date=target,fetched_at=core.iso(),**data_health.error_info(exc,(getattr(store,'key',''),)))
    store.put_json(key,outcome);return outcome
