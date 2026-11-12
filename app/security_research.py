"""Universal inventory-driven technical observations; no invented adjusted returns."""
import math
import re
import pandas as pd
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from . import core,indicators
from .strategy_data import profile


def resolve(query):
    query=query.strip().upper()
    with core.db() as c:
        rows=c.execute('SELECT * FROM instruments WHERE (symbol=? OR code=? OR name=?) ORDER BY active DESC',(query,query,query)).fetchall()
    if not rows:raise HTTPException(404,'未在证券名录找到该代码，请使用顶部搜索选择证券')
    if len(rows)>1 and rows[0]['symbol']!=rows[1]['symbol']:raise HTTPException(400,'证券名称有歧义，请使用完整代码')
    return dict(rows[0])


def capabilities(instrument):return profile(instrument['symbol'],instrument['name'],instrument['kind'],allow_research=True)


def coverage():
    counts={};risk=0
    with core.db() as c:rows=c.execute('SELECT * FROM instruments WHERE active=1').fetchall()
    for row in rows:
        p=capabilities(dict(row));label=p['board_label'];counts[label]=counts.get(label,0)+1;risk+=int(p['risk_warning'])
    return dict(total=len(rows),boards=counts,risk_warning=risk,note='名录覆盖不等于每只证券都有完整历史；单只证券按实际数据标注')


def finite(value):return float(value) if value is not None and math.isfinite(float(value)) else None


def analyze(payload,calendar):
    instrument=payload['instrument'];policy=capabilities(instrument);rows=payload['records']
    by_date={};issues=[]
    for row in rows:
        day=row['event_time'][:10]
        if not core.history_start()<=day<=core.now().astimezone(core.SH).date().isoformat():continue
        numbers=[row.get(k) for k in ('open','high','low','close')]
        if any(finite(x) is None or float(x)<=0 for x in numbers):issues.append(dict(date=day,reason='invalid_ohlc'));continue
        o,h,l,c=map(float,numbers)
        if not l<=min(o,c)<=max(o,c)<=h:issues.append(dict(date=day,reason='inconsistent_ohlc'));continue
        bar=dict(date=day,open=o,high=h,low=l,close=c,volume=finite(row.get('volume_raw',row.get('volume'))),source=row.get('source'))
        if day in by_date and by_date[day]!=bar:raise ValueError('同日行情存在冲突，拒绝拼接分析')
        by_date[day]=bar
    source_days=sorted(by_date)
    if not source_days:return dict(instrument=instrument,profile=policy,status='no_history',series=[],summary={},observations=[],quality=dict(valid_rows=0,missing_dates=[],issues=issues),quote=payload.get('quote'),note='当前无可用日线。可尝试更新该证券；新上市、长期停牌或源未覆盖时不会生成指标。')
    dates=[d for d in calendar if source_days[0]<=d<=source_days[-1]]
    if any(day not in dates for day in source_days):raise ValueError('行情包含交易日历以外日期，需先核验日历')
    frame=pd.DataFrame([by_date.get(d,dict(date=d)) for d in dates]).set_index('date')
    # Explicit zero-volume sessions are not valid indicator updates.
    zero=frame['volume'].eq(0)
    frame.loc[zero,['open','high','low','close','volume']]=float('nan')
    calculated=indicators.calculate(frame)
    calculated['date']=calculated.index
    series=calculated.where(pd.notna(calculated),None).to_dict('records')
    for bar in series:
        for key,value in list(bar.items()):
            if key not in ('date','source'):bar[key]=finite(value)
    latest=next((bar for bar in reversed(series) if bar['close'] is not None),None)
    summary=dict(latest or {})
    missing=[day for day in dates if day not in by_date]
    observations=[]
    def add(title,text,state='neutral'):observations.append(dict(title=title,text=text,state=state))
    if latest:
        price=latest['close'];ma=latest.get('ma60');rsi=latest.get('rsi14');volume=latest.get('volume_ratio20')
        if ma is not None:add('中期趋势',('价格高于' if price>ma else '价格低于')+'60日均线；这是历史价格位置，不是收益预测。','up' if price>ma else 'down')
        else:add('中期趋势','连续有效样本不足60日，暂不判断中期结构。')
        if rsi is not None:add('动量状态',f'RSI14 为 {rsi:.1f}，'+('处于常用超买区，但不代表马上下跌。' if rsi>=70 else '处于常用超卖区，但不代表马上反弹。' if rsi<=30 else '位于30—70常用观察区间。'))
        else:add('动量状态','有效样本不足，RSI保持空值。')
        if volume is not None:add('成交活跃度',f'原始成交量为此前20日均量的 {volume:.2f} 倍；仅比较同证券，不推断资金流向。')
        else:add('成交活跃度','成交量或连续20日样本不足，不推算量比。')
    if policy['risk_warning']:add('风险警示证券','可观察真实行情与指标；缺历史ST状态，不开放策略验证。','warning')
    return dict(instrument=instrument,profile=policy,status='ready' if latest and latest.get('ma60') is not None else 'limited_history',
                series=series,summary=summary,observations=observations,quote=payload.get('quote'),
                source=payload['source'],adjustment='不复权价格观察；不计算投资收益率',range=dict(start=dates[0],end=dates[-1],count=len(by_date)),
                freshness=payload.get('freshness'),quality=dict(valid_rows=len(by_date),missing_dates=missing,zero_volume_dates=list(frame.index[zero]),issues=issues),
                note='个股研究覆盖名录内所有A股与ETF；不复权指标可能受除权除息影响。缺失交易日不填价，递推指标重新预热；历史数据日期不等于实时行情。')


def routes(series_loader,page=None):
    router=APIRouter()
    @router.get('/stocks')
    def stocks_page():return page() if page else FileResponse(core.ROOT/'app/static/stocks/index.html')
    @router.get('/api/securities/coverage')
    def security_coverage():return coverage()
    @router.get('/api/securities/search')
    def search(q:str='',board:str='all',limit:int=30):
        if len(q)>60:raise HTTPException(400,'搜索内容过长')
        with core.db() as c:
            values=c.execute('SELECT * FROM instruments WHERE active=1 AND (instr(name,?)>0 OR instr(symbol,?)>0) ORDER BY symbol',(q.strip(),q.strip().upper())).fetchall()
        items=[]
        for value in values:
            a=dict(value);p=capabilities(a)
            if board!='all' and board!=p['board']:continue
            items.append(dict(symbol=a['symbol'],name=a['name'],kind=a['kind'],profile=p))
            if len(items)>=max(1,min(limit,100)):break
        return dict(items=items)
    @router.get('/api/securities/{query}/analysis')
    def security_analysis(query:str):
        instrument=resolve(query)
        try:return analyze(series_loader(instrument['symbol']),core.calendar_dates())
        except ValueError as exc:raise HTTPException(422,str(exc))
    return router
