"""Eastmoney industry indices: daily price returns, no synthetic constituents.
Interface follows AKShare stock_board_industry_name_em / hist_em documentation.
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

SOURCE='东方财富行业板块指数（AKShare 同源接口）'
DOC='https://akshare.akfamily.xyz/data/stock/stock_board.html'
NETWORK=threading.BoundedSemaphore(2)
CATALOG_LOCK=threading.Lock()

def request_json(url,params):
    with NETWORK:
        for attempt in range(2):
            try:
                r=requests.get(url,params=params,timeout=(4,10));r.raise_for_status();value=r.json();break
            except (requests.ConnectionError,requests.Timeout):
                if attempt:raise
                time.sleep(1.5)
    if value.get('rc')!=0 or not isinstance(value.get('data'),dict):raise ValueError('板块数据源返回空数据或异常格式')
    return value

def fetch_catalog():
    items={};total=None
    for page in range(1,21):
        if page>1:time.sleep(.6)  # Same paced pagination as AKShare; never burst the directory endpoint.
        raw=request_json('https://17.push2.eastmoney.com/api/qt/clist/get',dict(pn=page,pz=100,po=1,np=1,ut='bd1d9ddb04089700cf9c27f6f7426281',fltt=2,invt=2,fid='f3',fs='m:90 t:2 f:!50',fields='f12,f14'))
        data=raw['data'];total=int(data['total']);rows=data['diff']
        if not isinstance(rows,list) or not rows:raise ValueError('行业目录分页不完整')
        for row in rows:
            code=row['f12'];name=row['f14']
            if not re.fullmatch(r'BK\d{4,8}',code) or not isinstance(name,str) or not name.strip() or code in items:raise ValueError('行业目录代码重复或格式异常')
            items[code]=dict(code=code,name=name)
        if len(items)>=total:break
    if not total or len(items)!=total:raise ValueError('行业目录总量校验失败')
    return dict(items=sorted(items.values(),key=lambda x:x['code']),total=total,fetched_at=core.iso(),date=core.now().strftime('%Y-%m-%d'),source=SOURCE,documentation=DOC)

def catalog(store):
    key='system/industry/catalog.json.gz'
    with CATALOG_LOCK:
        cached=store.get_json(key,optional=True)
        if cached and cached.get('date')==core.now().strftime('%Y-%m-%d'):return dict(cached,stale=False)
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
    def fetch(code):
        key=f'system/industry/prices/{end}/{code}.json.gz';cached=store.get_json(key,optional=True)
        if cached and cached.get('start')==start and cached['rows'] and cached['rows'][-1][0]==end:
            validate_rows(cached['rows'],calendar);return code,cached
        raw=request_json('https://7.push2his.eastmoney.com/api/qt/stock/kline/get',dict(secid='90.'+code,fields1='f1,f2,f3,f4,f5,f6',fields2='f51,f52,f53,f54,f55,f56',klt=101,fqt=0,beg=start.replace('-',''),end=end.replace('-',''),smplmt=10000,lmt=1000000))
        data=raw['data']
        if data.get('code')!=code or not data.get('klines'):raise ValueError('板块代码不符或历史为空')
        rows=[r.split(',') for r in data['klines']];validate_rows(rows,calendar);rows.sort(key=lambda x:x[0])
        value=dict(name=known[code]['name'],rows=rows,start=start,end=end,fetched_at=core.iso(),source=SOURCE,evidence=raw)
        store.put_json(key,value);return code,value
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
    warnings=['使用东方财富行业板块指数原始点位（fqt=0），不是代表股拼接，也不是分红再投资总回报。','目录采用数据商行业及细分分类，可能有上下级或成分重叠；不是申万分类，未核验历史成分重叠。','协方差基于简单日收益率，样本估计分母 n−1；不对指数点位本身计算协方差。','滚动窗口重叠，负相关占比只作描述，不是统计显著性或获利概率。']
    if left==right:warnings.append('两边为同一板块；相关性不构成独立共振证据。')
    if metrics['end']!=snapshot['end']:warnings.append('共同样本未覆盖应有交易日，当前分析基于较早数据；请稍后重新生成。')
    if returns.isna().any(axis=1).any():warnings.append('所选区间存在缺失或较短历史，仅使用共同有效收益日；走势图断线处不填充。')
    selection=dict(kind='industry',left=dict(name=snapshot['assets'][left]['name'],symbols=[left]),right=dict(name=snapshot['assets'][right]['name'],symbols=[right]),lookback=lookback,rolling=rolling)
    return dict(version='industry-comparison-v1',selection=selection,metrics=metrics,windows=windows,negative_rolling_share=negative_share,rolling=[dict(date=d,value=finite(v)) for d,v in roll.items()],normalized=[dict(date=d,a=finite(r.A),b=finite(r.B)) for d,r in normalized.iterrows()],observations=[dict(date=d,a=finite(r.A),b=finite(r.B)) for d,r in returns.iterrows()],missing=[dict(date=d,symbols=[left if k=='A' else right for k in ('A','B') if pd.isna(r[k])]) for d,r in returns.iterrows() if r.isna().any()],quality=[dict(symbol=code,name=x['name'],rows=len(x['rows']),last=x['rows'][-1][0],source=SOURCE) for code,x in snapshot['assets'].items()],expected_end=snapshot['end'],warnings=warnings,interpretation=text,data_hash=hashlib.sha256(core.dumps(snapshot).encode()).hexdigest(),adjustment=SOURCE+'；指数原始点位；简单日收益；协方差为小数收益率²，年化×252',source_fetched_at=snapshot['fetched_at'])

class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    left:str=Field(pattern=r'^BK\d{4,8}$');right:str=Field(pattern=r'^BK\d{4,8}$');lookback:int=250;rolling:int=60

def routes(store_factory,user_for,check_action):
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
        except Exception:raise HTTPException(502,'行业行情获取或保存失败，请稍后重试；未生成替代数据')
        finally:lock.release()
    return router
