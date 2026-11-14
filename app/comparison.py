"""Two security/basket comparisons on a frozen, aligned adjusted-price snapshot."""
import hashlib,json,tempfile,threading,secrets
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,Field,ConfigDict
from . import core,tencent_source,daily_sync,security_research,strategy_service

PRESETS={'coal':{'name':'煤炭代表组合','symbols':['601088.SH','601225.SH','601898.SH','600188.SH','600985.SH','601699.SH']},'components':{'name':'元件代表组合','symbols':['002463.SZ','002384.SZ','002938.SZ','002916.SZ','600183.SH','300408.SZ']}}
LOOKBACKS=(0,60,120,126,250,252,756)
LOCKS=defaultdict(threading.Lock)
class Side(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(default='',max_length=40)
    symbols:list[str]=Field(min_length=1,max_length=10)
class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    left:Side
    right:Side
    lookback:int=0
    rolling:int=60

def finite(x):return round(float(x),12) if pd.notna(x) and np.isfinite(x) else None

def correlation(a,b):return finite(a.corr(b)) if a.std()>1e-12 and b.std()>1e-12 else None

def compute(snapshot,selection):
    calendar=snapshot['calendar'];prices={};quality=[]
    for symbol in sorted(snapshot['assets']):
        item=snapshot['assets'][symbol]
        values={}
        for cells in item['rows']:
            if len(cells)<6:raise ValueError('行情字段不完整')
            day=cells[0];o,c,h,l,v=map(float,cells[1:6])
            if day not in calendar or not all(np.isfinite([o,c,h,l,v])) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('复权行情或交易日校验失败：'+symbol)
            if day in values:raise ValueError('重复交易日：'+symbol)
            values[day]=c if v>0 else np.nan
        prices[symbol]=pd.Series(values,dtype=float)
        quality.append(dict(symbol=symbol,name=item['name'],rows=len(values),last=max(values) if values else None,zero_volume=sum(pd.isna(v) for v in values.values())))
    frame=pd.DataFrame(prices).reindex(calendar)
    returns=frame.pct_change(fill_method=None).replace([np.inf,-np.inf],np.nan)
    if selection['lookback']:returns=returns.tail(selection['lookback'])
    else:returns=returns.iloc[1:]
    left=selection['left']['symbols'];right=selection['right']['symbols']
    basket=pd.DataFrame({'A':returns[left].mean(axis=1,skipna=False),'B':returns[right].mean(axis=1,skipna=False),'market':returns['510300.SH']})
    aligned=basket.dropna();n=len(aligned)
    if n<30:raise ValueError(f'共同有效收益日只有{n}个，至少需要30个；缺失和停牌不会填价')
    def stats(f):
        x=np.column_stack([np.ones(len(f)),f['market']]);y=f[['A','B']].to_numpy();res=pd.DataFrame(y-x@np.linalg.lstsq(x,y,rcond=None)[0],columns=['A','B'])
        return dict(n=len(f),start=f.index[0],end=f.index[-1],covariance=finite(f.A.cov(f.B)),annualized_covariance=finite(f.A.cov(f.B)*252),correlation=correlation(f.A,f.B),residual_correlation=correlation(res.A,res.B),volatility_a=finite(f.A.std()*np.sqrt(252)),volatility_b=finite(f.B.std()*np.sqrt(252)),variance_a=finite(f.A.var()),variance_b=finite(f.B.var()))
    overall=stats(aligned);windows=[]
    for count in [60,126,252]:
        if count>len(basket):continue
        f=basket.tail(count).dropna()
        if len(f)>=30:windows.append(dict(window=count,**stats(f)))
    rolling=basket.A.rolling(selection['rolling'],min_periods=selection['rolling']).corr(basket.B)
    warnings=['后复权价格收益代理，未独立核验税后分红总回报。','组合固定等权，描述共同波动，不是扣费后的交易回测。','当前人工选择的存续证券有选择和存续偏差；代表组合不是官方行业指数。']
    if overall['end']<snapshot['end']:warnings.append('共同样本未覆盖应有交易日，请核对各证券数据日期；当前结果不是最新完整区间。')
    overlap=sorted(set(left)&set(right))
    if overlap:warnings.append('两边存在共同成分，可能机械性提高相关性：'+', '.join(overlap))
    if set(left)==set(right):warnings.append('两边成分相同；等权比较结果相同，不构成独立共振证据。')
    corr=overall['correlation']
    interpretation='收益几乎不变，相关系数无法定义。' if corr is None else ('样本期内同向共同波动较强。' if corr>=.6 else '样本期内有一定同向共同波动。' if corr>=.3 else '样本期内线性相关较弱。' if corr>-.3 else '样本期内有一定反向共同波动。' if corr>-.6 else '样本期内反向共同波动较强。')
    missing=[dict(date=day,symbols=[s for s in returns if pd.isna(returns.loc[day,s])]) for day in returns.index if returns.loc[day].isna().any()]
    digest=hashlib.sha256(core.dumps(snapshot).encode()).hexdigest()
    return dict(version='comparison-v1',selection=selection,metrics=overall,windows=windows,rolling=[dict(date=d,value=finite(v)) for d,v in rolling.items()],observations=[dict(date=d,a=finite(r.A),b=finite(r.B),market=finite(r.market)) for d,r in basket.iterrows()],quality=quality,missing=missing,expected_end=snapshot['end'],warnings=warnings,interpretation=interpretation+'相关性会变化，不说明因果或明天的涨跌。',data_hash=digest,adjustment='腾讯后复权日线；简单日收益率；协方差单位为小数收益率的平方；年化乘252',source_fetched_at=snapshot['fetched_at'])

def acquire(symbols,store):
    end=daily_sync.freshness(None,core.calendar_dates())['expected_date']
    if not end:raise ValueError('当前交易日历不足，无法确认应有日线日期')
    start=core.history_start();calendar=[d for d in core.calendar_dates() if start<=d<=end]
    def fetch(symbol):
        instrument=core.instrument(symbol);key='system/comparison-prices/'+end+'/'+symbol+'.json.gz'
        cached=store.get_json(key,optional=True)
        if cached and cached.get('start')==start:return symbol,cached
        with tempfile.TemporaryDirectory() as path:
            spec=tencent_source.fetch_one((symbol,2),Path(path),start,end)
            raw=(Path(path)/spec['file']).read_bytes();payload=json.loads(raw)
            rows,name=tencent_source.rows_from_evidence(payload,symbol,2,start,end)
            value=dict(name=name or instrument['name'],rows=rows,source=spec,evidence=payload,start=start,end=end)
        store.put_json(key,value);return symbol,value
    with ThreadPoolExecutor(max_workers=2) as pool:assets=dict(pool.map(fetch,symbols))
    return dict(calendar=calendar,start=start,end=end,fetched_at=max(a['source']['fetched_at'] for a in assets.values()),assets=assets)

def routes(store_factory,user_for,check_action,page=None):
    router=APIRouter()
    @router.get('/compare')
    def page_route():return page() if page else FileResponse(core.ROOT/'app/static/compare/index.html')
    @router.get('/api/comparison/presets')
    def presets():
        return {'items':[dict(id=k,name=v['name'],members=[dict(symbol=s,name=(core.instrument(s) or {}).get('name',s)) for s in v['symbols']]) for k,v in PRESETS.items()], 'note':'可编辑代表组合，非完整行业成分或官方指数'}
    @router.post('/api/comparison/run')
    def run(request:Request,body:Selection):
        check_action(request)
        if body.lookback not in LOOKBACKS or body.rolling not in (20,60,120):raise HTTPException(400,'不支持的时间窗口')
        selection=body.model_dump()
        for side in ('left','right'):
            selection[side]['symbols']=list(dict.fromkeys(security_research.resolve(s)['symbol'] for s in selection[side]['symbols']))
            if not selection[side]['name']:selection[side]['name']=core.instrument(selection[side]['symbols'][0])['name'] if len(selection[side]['symbols'])==1 else ('组合A' if side=='left' else '组合B')
        user=user_for(request);lock=LOCKS[user]
        if not lock.acquire(False):raise HTTPException(409,'已有比较正在计算，请稍候')
        try:
            store=store_factory();symbols=list(dict.fromkeys(selection['left']['symbols']+selection['right']['symbols']+['510300.SH']))
            snapshot=acquire(symbols,store);result=compute(snapshot,selection)
            report_id=core.now().strftime('%Y%m%dT%H%M%S%f')+'-'+secrets.token_hex(8)
            record=dict(id=report_id,created_at=core.iso(),result=result,snapshot=snapshot,code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            store.put_json('users/'+user+'/comparisons/'+report_id+'.json.gz',record)
            return dict(id=report_id,result=result)
        except ValueError as exc:raise HTTPException(422,str(exc))
        except Exception:raise HTTPException(502,'复权数据源或云端保存暂不可用；未生成不完整比较，请稍后重试')
        finally:lock.release()
    @router.get('/api/comparison/reports/{report_id}/download')
    def download(request:Request,report_id:str):
        report_id=strategy_service.identifier(report_id);record=store_factory().get_json('users/'+user_for(request)+'/comparisons/'+report_id+'.json.gz',optional=True)
        if not record:raise HTTPException(404,'当前账号没有该报告')
        return Response(core.dumps(record),media_type='application/json',headers={'Content-Disposition':'attachment; filename="comparison-'+report_id+'.json"'})
    return router
