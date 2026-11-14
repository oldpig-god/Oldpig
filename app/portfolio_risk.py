"""Two indices, initial allocation then buy-and-hold; explicit entry/exit costs."""
import math,statistics

def summarize(values):
    returns=[b/a-1 for a,b in zip(values,values[1:])]
    peak=values[0];drawdown=[]
    for v in values:
        peak=max(peak,v);drawdown.append(v/peak-1)
    return dict(total_return=values[-1]/values[0]-1,volatility=statistics.stdev(returns)*math.sqrt(252) if len(returns)>1 else None,max_drawdown=min(drawdown))

def calculate(observations,weight=.5,cost_bps=10):
    if not 0<=weight<=1 or not 0<=cost_bps<=100:raise ValueError('权重或费率超出范围')
    if len(observations)<30:raise ValueError('组合计算需要至少30个收益日')
    missing=sum(x['a'] is None or x['b'] is None for x in observations)
    if missing:return dict(available=False,message=f'区间缺失{missing}个收益日，缩短区间后可计算组合')
    fee=cost_bps/10000;a=b=1.;series=[];values={'a':[1.],'b':[1.],'portfolio':[1.]}
    for i,row in enumerate(observations):
        a*=1+row['a'];b*=1+row['b'];mult=(1-fee)*(1-fee if i==len(observations)-1 else 1)
        point=dict(date=row['date'],a=a*mult,b=b*mult,portfolio=(weight*a+(1-weight)*b)*mult)
        series.append(point)
        for key in values:values[key].append(point[key])
    metrics={k:summarize(v) for k,v in values.items()}
    base=weight*metrics['a']['volatility']+(1-weight)*metrics['b']['volatility']
    return dict(available=True,weight=weight,cost_bps=cost_bps,metrics=metrics,series=series,volatility_reduction=1-metrics['portfolio']['volatility']/base if base else None,assumption='指数价格模拟；初始配置后持有，期间不调仓；首尾各收取设定费率；不含分红')
