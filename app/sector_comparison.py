"""THS industry indices: daily price returns, no synthetic constituents.
Interface follows AKShare stock_board_industry_name_ths / index_ths documentation.
"""
import hashlib,re,secrets,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
from . import core,daily_sync,comparison

from . import industry_ths
SOURCE=industry_ths.SOURCE
DOC=industry_ths.DOC
CATALOG_LOCK=threading.Lock()
fetch_catalog=industry_ths.fetch_catalog

def catalog(store):
    key=industry_ths.PREFIX+'catalog.json.gz'
    with CATALOG_LOCK:
        cached=store.get_json(key,optional=True)
        if cached and cached.get('date')==core.now().astimezone(core.SH).strftime('%Y-%m-%d'):return dict(cached,stale=False)
        try:
            value=fetch_catalog();store.put_json(key,value);return dict(value,stale=False)
        except Exception:
            if cached:return dict(cached,stale=True,notice='行业目录刷新失败，展示上次成功目录；历史行情仍单独校验')
            raise

def validate_rows(rows,calendar):
    values={};allowed=set(calendar)
    for cells in rows:
        if len(cells)<6:raise ValueError('板块行情字段不足')
        day=cells[0];o,c,h,l,v=map(float,cells[1:6])
        if day not in allowed or day in values or not all(np.isfinite([o,c,h,l,v])) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('板块行情交易日、重复或价格校验失败')
        values[day]=c if v>0 else np.nan
    return pd.Series(values,dtype=float).reindex(calendar)

def acquire(codes,store,listing):
    end=daily_sync.freshness(None,core.calendar_dates())['expected_date']
    if not end:raise ValueError('交易日历不足，无法确定应有日线日期')
    start=core.history_start();calendar=[d for d in core.calendar_dates() if start<=d<=end];known={x['code']:x for x in listing['items']}
    def fetch(code):return code,industry_ths.fetch_asset(code,known[code]['name'],store,start,end,calendar,validate_rows)
    with ThreadPoolExecutor(max_workers=2) as pool:assets=dict(pool.map(fetch,dict.fromkeys(codes)))
    return dict(calendar=calendar,start=start,end=end,assets=assets,fetched_at=max(x['fetched_at'] for x in assets.values()),catalog_fetched_at=listing['fetched_at'])

def compute(snapshot,left,right,lookback=0,rolling=60):
    calendar=snapshot['calendar'];a=validate_rows(snapshot['assets'][left]['rows'],calendar);b=validate_rows(snapshot['assets'][right]['rows'],calendar)
    prices=pd.DataFrame({'A':a,'B':b})
    if lookback:prices=prices.tail(lookback+1)
    returns=prices.pct_change(fill_method=None).iloc[1:];aligned=returns.dropna()
    if len(aligned)<30:raise ValueError(f'共同有效收益日只有 {len(aligned)} 个，至少需要30个；暂不判断，不填补缺失')
    finite=comparison.finite
    def stats(f):return dict(n=len(f),start=f.index[0],end=f.index[-1],covariance=finite(f.A.cov(f.B)),annualized_covariance=finite(f.A.cov(f.B)*252),correlation=comparison.correlation(f.A,f.B),residual_correlation=None,volatility_a=finite(f.A.std()*np.sqrt(252)),volatility_b=finite(f.B.std()*np.sqrt(252)),variance_a=finite(f.A.var()),variance_b=finite(f.B.var()))
    metrics=stats(aligned);windows=[]
    for size in (60,120,250):
        if len(returns)>=size:
            f=returns.tail(size).dropna()
            if len(f)>=30:windows.append(dict(window=size,**stats(f)))
    roll=returns.A.rolling(rolling,min_periods=rolling).corr(returns.B).clip(-1,1)
    # Normalize price levels at one common anchor; never compound a gapped return series.
    valid_prices=prices.dropna();anchor=valid_prices.index[0];normalized=prices.loc[anchor:].divide(valid_prices.iloc[0])*100
    corr=metrics['correlation'];text='任一板块收益波动过低，相关系数不可定义。' if corr is None else ('样本期整体呈反向共同波动。' if corr<-.3 else '样本期线性关联较弱。' if corr<.3 else '样本期整体呈同向共同波动。')
    valid_roll=roll.dropna();negative_share=finite((valid_roll<0).mean()) if len(valid_roll) else None
    if negative_share is not None:text+=f'可计算的{rolling}日滚动窗口中，负相关占 {negative_share:.1%}。'
    text+='相关性描述历史关系，会随时间变化，不证明因果或未来对冲有效。'
    warnings=['使用同花顺行业指数原始点位，不是代表股拼接，也不是分红再投资总回报。','目录采用同花顺行业分类；不与东方财富或申万代码混用，未核验历史成分重叠。','协方差基于简单日收益率，样本估计分母 n−1；不对指数点位本身计算协方差。','滚动窗口重叠，负相关占比只作描述，不是统计显著性或获利概率。']
    for asset in snapshot['assets'].values():
        if asset.get('refresh_error'):warnings.append(asset['name']+'：'+asset['refresh_error'])
    if left==right:warnings.append('两边为同一板块；相关性不构成独立共振证据。')
    if metrics['end']!=snapshot['end']:warnings.append('共同样本未覆盖应有交易日，当前分析基于较早数据；请稍后重新生成。')
    if returns.isna().any(axis=1).any():warnings.append('所选区间存在缺失或较短历史，仅使用共同有效收益日；走势图断线处不填充。')
    selection=dict(kind='industry',left=dict(name=snapshot['assets'][left]['name'],symbols=[left]),right=dict(name=snapshot['assets'][right]['name'],symbols=[right]),lookback=lookback,rolling=rolling)
    return dict(version='industry-comparison-ths-v2',selection=selection,metrics=metrics,windows=windows,negative_rolling_share=negative_share,rolling=[dict(date=d,value=finite(v)) for d,v in roll.items()],normalized=[dict(date=d,a=finite(r.A),b=finite(r.B)) for d,r in normalized.iterrows()],observations=[dict(date=d,a=finite(r.A),b=finite(r.B)) for d,r in returns.iterrows()],missing=[dict(date=d,symbols=[left if k=='A' else right for k in ('A','B') if pd.isna(r[k])]) for d,r in returns.iterrows() if r.isna().any()],quality=[dict(symbol=code,name=x['name'],rows=len(x['rows']),last=x['rows'][-1][0],source=SOURCE) for code,x in snapshot['assets'].items()],expected_end=snapshot['end'],warnings=warnings,interpretation=text,data_hash=hashlib.sha256(core.dumps(snapshot).encode()).hexdigest(),adjustment=SOURCE+'；指数原始点位；简单日收益；协方差为小数收益率²，年化×252',source_fetched_at=snapshot['fetched_at'])

class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    left:str=Field(pattern=r'^THS\d{6}$');right:str=Field(pattern=r'^THS\d{6}$');lookback:int=250;rolling:int=60

def routes(store_factory,user_for,check_action,record_failure=None):
    router=APIRouter()
    @router.get('/api/comparison/industries')
    def industries():
        try:return catalog(store_factory())
        except Exception:raise HTTPException(502,'行业目录暂不可用，请稍后重试；未使用虚构板块')
    @router.post('/api/comparison/industries/run')
    def run(body:Selection,request:Request):
        check_action(request)
        if body.lookback not in (0,60,120,126,250,252,756) or body.rolling not in (20,60,120):raise HTTPException(400,'不支持的窗口')
        user=user_for(request);lock=comparison.LOCKS[user]
        if not lock.acquire(False):raise HTTPException(409,'已有比较正在计算，请稍候')
        try:
            store=store_factory();listing=catalog(store);known={x['code'] for x in listing['items']}
            if body.left not in known or body.right not in known:raise ValueError('所选代码不在行业目录中，请刷新目录')
            snapshot=acquire([body.left,body.right],store,listing);result=compute(snapshot,body.left,body.right,body.lookback,body.rolling)
            if listing.get('stale'):result['warnings'].append(listing['notice'])
            report_id=core.now().strftime('%Y%m%dT%H%M%S%f')+'-'+secrets.token_hex(8)
            store.put_json('users/'+user+'/comparisons/'+report_id+'.json.gz',dict(id=report_id,created_at=core.iso(),result=result,snapshot=snapshot,code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
            return dict(id=report_id,result=result)
        except ValueError as exc:raise HTTPException(422,str(exc))
        except Exception as exc:
            reference=record_failure('industry_comparison',exc) if record_failure else None
            raise HTTPException(502,'行业行情获取或保存失败，请稍后重试；未生成替代数据'+('；诊断编号 '+reference if reference else ''))
        finally:lock.release()
    return router
