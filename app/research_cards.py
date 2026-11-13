"""Explainable end-of-day research. No rankings, forecasts or filled observations."""
import hashlib
import math
import numpy as np
import pandas as pd
from . import core, indicators

VERSION='research-card-v1'
BENCHMARK='510300.SH'
RULES=dict(trend_window=60,trend_slope_days=5,momentum_window=20,rsi_strong=55,rsi_weak=45,
           volatility_window=20,volatility_reference=252,volatility_min_samples=126,
           volatility_high_percentile=80,volatility_low_percentile=20,volume_high=1.5,volume_low=.8,relative_window=60)

def value(x):return round(float(x),8) if x is not None and math.isfinite(float(x)) else None

def frame(asset,calendar):
    rows={}
    for cells in asset['rows']:
        if len(cells)<6:raise ValueError('后复权行情字段不足')
        day=cells[0];o,c,h,l,v=map(float,cells[1:6])
        if day not in calendar or not all(math.isfinite(x) for x in (o,c,h,l,v)) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('后复权行情或交易日校验失败')
        if day in rows:raise ValueError('同日行情重复，拒绝判断')
        rows[day]=dict(open=o,high=h,low=l,close=c,volume=v)
    result=pd.DataFrame.from_dict(rows,orient='index',columns=['open','high','low','close','volume']).reindex(calendar)
    result.loc[result.volume.eq(0),:]=np.nan
    return result

def build(snapshot,symbol):
    calendar=snapshot['calendar'];asset=snapshot['assets'][symbol];f=frame(asset,calendar);bench=frame(snapshot['assets'][BENCHMARK],calendar)
    data=indicators.calculate(f);last=data.iloc[-1] if len(data) else pd.Series(dtype=float)
    valid=f.close.notna();consecutive=0
    for ok in valid.iloc[::-1]:
        if not ok:break
        consecutive+=1
    last_date=next((d for d in reversed(calendar) if pd.notna(f.loc[d,'close'])),None)
    usable=bool(calendar and last_date==snapshot['end']);items=[]
    def item(key,title,state,text,formula,values,required,observed,reason=None,window=None):
        enough=usable and observed>=required and not reason
        items.append(dict(key=key,title=title,state=state if enough else 'unknown',judgment=text if enough else '暂不判断',
            reason=None if enough else reason or ('未覆盖应有交易日' if not usable else f'需要 {required} 个有效样本，当前 {observed} 个'),
            evidence=dict(formula=formula,values={k:value(v) for k,v in values.items()},required=required,observed=min(observed,window or required),available=observed,
                start=calendar[max(0,len(calendar)-(window or required))] if calendar else None,end=snapshot['end'],adjustment='后复权',source='腾讯证券',rules_version=VERSION)))
    close=value(last.get('close'));ma20=value(last.get('ma20'));ma60=value(last.get('ma60'))
    past=value(data.ma60.iloc[-6]) if len(data)>=65 else None;slope=(ma60/past-1)*100 if ma60 is not None and past else None
    trend='up' if all(x is not None for x in [close,ma20,ma60,slope]) and close>ma20>ma60 and slope>0 else 'down' if all(x is not None for x in [close,ma20,ma60,slope]) and close<ma20<ma60 and slope<0 else 'mixed'
    item('trend','趋势是否一致？',trend,{'up':'价格、均线排列与中期斜率同向上行','down':'价格、均线排列与中期斜率同向下行','mixed':'趋势证据未形成一致方向'}[trend],
        '收盘价 > MA20 > MA60 且 MA60 相比5个交易日前上升：上行；全部反向：下行；其他：混合。',dict(close=close,ma20=ma20,ma60=ma60,ma60_slope5_pct=slope),65,consecutive)
    roc=(f.close.iloc[-1]/f.close.iloc[-21]-1)*100 if consecutive>=21 else None;rsi=value(last.get('rsi14'))
    momentum='strong' if rsi is not None and roc is not None and rsi>=55 and roc>0 else 'weak' if rsi is not None and roc is not None and rsi<=45 and roc<0 else 'mixed'
    item('momentum','上涨或下跌是否有动量支持？',momentum,{'strong':'20日价格变化与 RSI 支持正向动量','weak':'20日价格变化与 RSI 支持负向动量','mixed':'动量证据中性或不一致'}[momentum],
        'ROC20=(P今日/P20日前−1)×100；RSI14 使用 Wilder 平滑。RSI≥55且ROC>0为正向，RSI≤45且ROC<0为负向。',dict(roc20_pct=roc,rsi14=rsi),21,consecutive)
    reference=data.realized_vol20_pct.iloc[-252:].dropna();vol=value(last.get('realized_vol20_pct'));percentile=float((reference<=vol).mean()*100) if len(reference)>=126 and vol is not None else None
    risk='high' if percentile is not None and percentile>=80 else 'low' if percentile is not None and percentile<=20 else 'normal'
    item('volatility','当前波动处于自身什么位置？',risk,{'high':'波动处于自身历史较高区间','low':'波动处于自身历史较低区间','normal':'波动处于自身历史中间区间'}[risk],
        '20日对数收益样本标准差×√252；在最近252个交易日可用波动率中计算经验百分位（至少126个），≥80为偏高，≤20为偏低。不是跨股票风险评级。',
        dict(annualized_vol20_pct=vol,percentile=percentile,atr14_pct=value(last.get('atr_pct'))),126,len(reference),reason='最近21个交易日不连续，波动率不可用' if consecutive<21 else None,window=252)
    ratio=value(last.get('volume_ratio20'));activity='active' if ratio is not None and ratio>=1.5 else 'quiet' if ratio is not None and ratio<.8 else 'normal'
    item('activity','成交是否比自身平时活跃？',activity,{'active':'成交量明显高于此前20日均量','quiet':'成交量低于此前20日常态','normal':'成交量接近此前20日常态'}[activity],
        '今日原始成交量 / 此前20个交易日成交量均值（不含今日）；≥1.5为活跃，<0.8为偏淡。同源同证券比较，不解释为资金流入。',dict(volume_ratio20=ratio),21,int(f.volume.tail(21).gt(0).sum()),reason='成交量口径或窗口不可用' if ratio is None else None)
    joint=pd.concat([f.close.rename('stock'),bench.close.rename('benchmark')],axis=1).tail(61);n=int(joint.notna().all(axis=1).sum());a=b=excess=None
    if len(joint)==61 and n==61:
        a=(joint.stock.iloc[-1]/joint.stock.iloc[0]-1)*100;b=(joint.benchmark.iloc[-1]/joint.benchmark.iloc[0]-1)*100;excess=a-b
    relative='outperform' if excess is not None and excess>0 else 'underperform' if excess is not None and excess<0 else 'equal'
    item('relative','是否跑赢沪深300 ETF？',relative,{'outperform':'近60个交易日价格表现强于宽基','underperform':'近60个交易日价格表现弱于宽基','equal':'近60个交易日价格表现与宽基持平'}[relative],
        '同一连续61个交易日端点，分别计算后复权价格变化；股票变化减沪深300ETF(510300.SH)变化，单位百分点。是价格收益代理，不是税后分红总回报。',
        dict(stock_change60_pct=a,benchmark_change60_pct=b,excess_percentage_points=excess),61,n)
    states={x['key']:x['state'] for x in items};conflicts=[]
    def conflict(keys,text):conflicts.append(dict(keys=keys,text=text))
    if states['trend']=='up' and states['momentum']=='weak':conflict(['trend','momentum'],'中期趋势向上，但短期动量转弱。')
    if states['trend']=='down' and states['momentum']=='strong':conflict(['trend','momentum'],'短期动量转强，但中期下行结构尚未改变。')
    if states['trend']=='up' and states['relative']=='underperform':conflict(['trend','relative'],'自身趋势向上，但近60日仍落后宽基。')
    if states['trend']=='down' and states['relative']=='outperform':conflict(['trend','relative'],'自身趋势向下，但近60日仍强于宽基；相对强不等于绝对上涨。')
    if states['trend']=='up' and states['activity']=='quiet':conflict(['trend','activity'],'上行结构伴随偏淡成交；成交没有同步增强，不等于趋势必然反转。')
    if states['trend']=='up' and states['volatility']=='high':conflict(['trend','volatility'],'趋势向上同时波动偏高；方向与风险是不同维度。')
    fingerprint=hashlib.sha256(core.dumps(dict(calendar=calendar,rows=asset['rows'],benchmark=snapshot['assets'][BENCHMARK]['rows'],rules=RULES)).encode()).hexdigest()
    return dict(symbol=symbol,name=asset['name'],asof=last_date,expected_date=snapshot['end'],generated_at=core.iso(),usable=usable,items=items,states=states,conflicts=conflicts,
        quality=dict(consecutive_sessions=consecutive,total_valid=int(valid.sum()),missing_recent=[d for d in calendar[-65:] if not valid.loc[d]]),
        rules=RULES,version=VERSION,data_hash=fingerprint,source_fetched_at=snapshot['fetched_at'],benchmark=BENCHMARK,
        chart=[dict(date=d,close=value(data.loc[d,'close']),ma20=value(data.loc[d,'ma20']),ma60=value(data.loc[d,'ma60'])) for d in calendar],
        notes=['全部判断基于日线后复权行情；不生成买卖建议或综合得分。','历史样本不足、缺口或过期时对应维度不判断。','未观察到矛盾不代表信号已验证有效。'])

def transition(previous,current):
    """State transitions only; never one notification per individual indicator value."""
    now=dict(valid=bool(current['usable']),risk=current['states']['volatility'],conditions={k:current['states'][k] for k in ('trend','momentum','activity','relative')},version=current['version'])
    changes=[]
    if previous and previous.get('version')==now['version']:
        if previous['valid']!=now['valid']:changes.append(dict(kind='data',title='数据已恢复可用' if now['valid'] else '数据已失效',before=previous['valid'],after=now['valid']))
        if now['valid'] and previous['valid']:
            if previous['risk']!=now['risk']:changes.append(dict(kind='risk',title='波动风险档位变化',before=previous['risk'],after=now['risk']))
            if previous['conditions']!=now['conditions']:changes.append(dict(kind='conditions',title='研究条件发生变化',before=previous['conditions'],after=now['conditions']))
    return now,changes
