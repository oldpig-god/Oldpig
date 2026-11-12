"""Causal daily-bar simulation, explicit assumptions and deterministic diagnostics.

No indicator can inspect a bar after its signal date. No optimizer is included.
Only explicit verified corporate events can change share counts/cash. Public
adjusted prices generate signals; raw prices generate executable price-only P&L.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import math
import random
import statistics
from pathlib import Path
from .strategy_data import validate, digest

ENGINE_VERSION='strategy-v1.1'


@dataclass(frozen=True)
class Config:
    fast:int=20
    slow:int=60
    capital:float=100000
    commission:float=.0003
    minimum:float=5
    stamp:float=.0005
    historical_stamp:bool=True
    transfer:float=.00001
    slippage:float=.0005
    execution:str='open'
    train_fraction:float=.7
    risk_free:float=0
    seed:int=20261112
    random_runs:int=100
    def checked(self):
        if not 5<=self.fast<=100 or not self.fast+3<=self.slow<=250:raise ValueError('均线须满足 5≤短线≤100，短线+3≤长线≤250')
        if not 10000<=self.capital<=100000000:raise ValueError('初始资金须为1万至1亿元')
        if self.execution not in ('open','close'):raise ValueError('成交时点仅支持次日开盘或次日收盘')
        if not .5<=self.train_fraction<=.8:raise ValueError('形成期比例须为50%至80%')
        for key in ('commission','stamp','transfer','slippage'):
            if not 0<=getattr(self,key)<=.01:raise ValueError('费用/滑点比率超出范围')
        if not 0<=self.minimum<=100 or not 0<=self.risk_free<=.2:raise ValueError('最低佣金或无风险收益率超出范围')
        if not 20<=self.random_runs<=200 or not 0<=self.seed<2**32:raise ValueError('随机次数须为20至200，种子须为32位非负整数')
        if not all(math.isfinite(v) for v in asdict(self).values() if isinstance(v,(int,float))):raise ValueError('配置必须为有限值')
        return self


def code_hash():
    root=Path(__file__).parent
    return hashlib.sha256(b''.join((root/f).read_bytes() for f in ('strategy.py','strategy_data.py'))).hexdigest()


def round_tick(price,tick):
    return float((Decimal(str(price))/Decimal(str(tick))).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*Decimal(str(tick)))


def signals(bars,fast,slow):
    result=[];window=[]
    for bar in bars:
        if bar is None or bar['volume']==0:window=[];result.append(None);continue
        window.append(bar['adjusted_close']);window=window[-slow:]
        result.append(int(sum(window[-fast:])/fast>sum(window)/slow) if len(window)==slow else None)
    return result


def costs(value,side,day,asset,cfg):
    commission=round(max(cfg.minimum,value*cfg.commission),2)
    stamp=0
    if side=='sell' and asset['kind']=='stock':
        rate=(.001 if day<'2023-08-28' else .0005) if cfg.historical_stamp else cfg.stamp
        stamp=round(value*rate,2)
    transfer=round(value*cfg.transfer,2) if asset['kind']=='stock' else 0
    return dict(commission=commission,stamp=stamp,transfer=transfer,total=round(commission+stamp+transfer,2))


def metrics(curve,trades,capital,cfg):
    values=[capital]+[p['equity'] for p in curve];returns=[b/a-1 for a,b in zip(values,values[1:])]
    n=len(curve)
    if not n:return {}
    total=values[-1]/capital-1;annual=(values[-1]/capital)**(252/n)-1
    vol=statistics.stdev(returns)*math.sqrt(252) if n>1 else 0
    daily_rf=(1+cfg.risk_free)**(1/252)-1
    sharpe=(statistics.mean(returns)-daily_rf)*252/vol if vol else None
    drawdown=min(p['drawdown'] for p in curve)
    filled=[t for t in trades if t['status']=='filled'];closed=[t for t in filled if t['side']=='sell']
    profits=[t['pnl'] for t in closed];wins=[p for p in profits if p>0];losses=[p for p in profits if p<0]
    return dict(total_return=total,annual_return=annual,max_drawdown=drawdown,volatility=vol,sharpe=sharpe,
                calmar=annual/abs(drawdown) if drawdown else None,turnover=sum(t['notional'] for t in filled)/statistics.mean(values[1:]),
                annual_turnover=sum(t['notional'] for t in filled)/statistics.mean(values[1:])*252/n,
                trades=len(filled),round_trips=len(closed),average_holding_days=statistics.mean(t['holding_days'] for t in closed) if closed else None,
                average_exposure=statistics.mean(p['exposure'] for p in curve),win_rate=len(wins)/len(profits) if profits else None,
                payoff_ratio=(statistics.mean(wins)/abs(statistics.mean(losses))) if wins and losses else None,
                fees=sum(t['fees']['total'] for t in filled),slippage_cost=sum(t['slippage_cost'] for t in filled),
                unfilled=sum(t['status']=='blocked' for t in trades),sessions=n)


def simulate(asset,dates,target,cfg,start=0,end=None,weight=1.,entry_mode=False):
    """Signal at i-1 only. Missing bars stay missing; stale marks are explicitly flagged.

    Missing corporate-event ledgers are NEVER inferred from adjusted/raw ratios.
    Optional verified events supply split_ratio, cash_per_share and payment_date.
    """
    bars=asset['bars'];end=len(dates) if end is None else end
    cash=cfg.capital;qty=0.;basis=0.;entry=None;last_price=None;dividend_income=0.;receivables=[]
    equity=cfg.capital;peak=equity;curve=[];trades=[];events=[];bought=False
    # The prior factor/price is known before evaluation begins; never create a holding.
    for b in bars[:start]:
        if b is not None:last_price=b['close']
    for i in range(start,end):
        bar=bars[i];day=dates[i];prior_equity=equity
        event=asset.get('corporate_actions',{}).get(day)
        for due in list(receivables):
            if due['date']<=day:cash+=due['amount'];receivables.remove(due)
        if event:
            if not asset.get('corporate_actions_verified'):raise ValueError('企业行动账本必须标记核验')
            if qty:
                dividend=qty*event.get('cash_per_share',0);dividend_income+=dividend
                payment_date=event.get('payment_date',day)
                if payment_date<=day:cash+=dividend
                else:receivables.append(dict(date=payment_date,amount=dividend))
                qty*=event.get('split_ratio',1)
                if bar is None and last_price is not None:last_price=(last_price-event.get('cash_per_share',0))/event.get('split_ratio',1)
            events.append(dict(date=day,kind='verified_corporate_action',event=event))
        wanted=target[i-1] if i>0 else None
        if entry_mode:wanted=1 if not bought else 1
        action='buy' if wanted==1 and qty==0 and (not entry_mode or not bought) else 'sell' if wanted==0 and qty>0 else None
        action_day=bool(event) and not (bar and 'limit_up' in bar and 'limit_down' in bar)
        reason=None
        if action:
            if bar is None:reason='missing_session_or_suspension'
            elif bar['volume']==0:reason='suspended_zero_volume'
            elif action_day:reason='corporate_action_limits_missing'
            elif i==0 or bars[i-1] is None:reason='previous_session_missing'
            elif action=='sell' and entry==i:reason='T_plus_1'
            else:
                price=bar[cfg.execution];previous=bars[i-1]['close']
                upper=bar.get('limit_up',round_tick(previous*(1+asset['limit_rate']),asset['tick']))
                lower=bar.get('limit_down',round_tick(previous*(1-asset['limit_rate']),asset['tick']))
                # Conservative: no buying at limit-up / selling at limit-down,
                # even if a daily candle later opens. No fabricated intraday liquidity.
                if action=='buy' and price>=upper-asset['tick']/2:reason='limit_up'
                elif action=='sell' and price<=lower+asset['tick']/2:reason='limit_down'
                else:
                    fill=round_tick(price*(1+cfg.slippage*(1 if action=='buy' else -1)),asset['tick'])
                    if fill<bar['low'] or fill>bar['high'] or fill>upper or fill<lower:reason='slippage_outside_observed_range'
                    else:
                        if action=='buy':
                            budget=cash*weight
                            quantity=min(asset.get('max_buy',1000000),math.floor(budget/(fill*(1+cfg.commission+cfg.transfer))/asset['lot'])*asset['lot'])
                            while quantity>0 and quantity*fill+costs(quantity*fill,action,day,asset,cfg)['total']>budget:quantity-=asset['lot']
                            if quantity<asset.get('min_buy',asset['lot']):reason='insufficient_cash_for_lot'
                        else:quantity=qty
                        if reason is None:
                            notional=quantity*fill;fee=costs(notional,action,day,asset,cfg)
                            trade=dict(date=day,signal_date=dates[i-1] if i else None,side=action,status='filled',price=fill,quantity=quantity,
                                       quantity_basis='实际股数（仅核验送转事件可改变持仓）',fees=fee,notional=notional,slippage_cost=abs(fill-price)*quantity,reason=None)
                            if action=='buy':
                                cash-=notional+fee['total'];qty=quantity;basis=notional+fee['total'];entry=i;bought=True;dividend_income=0.
                            else:
                                cash+=notional-fee['total'];trade.update(pnl=notional-fee['total']-basis+dividend_income,holding_days=i-entry,entry_index=entry,exit_index=i);qty=0.;basis=0.;entry=None
                            trades.append(trade)
            if reason:trades.append(dict(date=day,signal_date=dates[i-1] if i else None,side=action,status='blocked',reason=reason,price=None,quantity=0,fees=dict(total=0)))
        if bar:last_price=bar['close']
        equity=cash+qty*(last_price or 0)+sum(p['amount'] for p in receivables);peak=max(peak,equity)
        curve.append(dict(date=day,equity=equity,nav=equity/cfg.capital,drawdown=equity/peak-1,cash=cash,quantity=qty,
                          exposure=qty*(last_price or 0)/equity if equity else 0,valuation='stale_mark_missing_bar' if bar is None else 'suspended_mark' if bar['volume']==0 else 'observed',
                          daily_return=equity/prior_equity-1))
    return dict(curve=curve,trades=trades,metrics=metrics(curve,trades,cfg.capital,cfg),events=events,
                open_position=qty>0,terminal_liquidation=False)


def compare(asset,benchmark,dates,target,cfg,start,end):
    primary=simulate(asset,dates,target,cfg,start,end)
    exposure=primary['metrics']['average_exposure']
    buyhold=simulate(asset,dates,target,cfg,start,end,entry_mode=True)
    etf=simulate(benchmark,dates,target,cfg,start,end,entry_mode=True)
    matched=simulate(asset,dates,target,cfg,start,end,weight=exposure,entry_mode=True)
    primary['benchmarks']={'buy_hold':buyhold,'etf':etf,'exposure_matched':matched}
    primary['excess']={name:primary['metrics']['total_return']-value['metrics']['total_return'] for name,value in primary['benchmarks'].items()}
    primary['matched_initial_weight']=exposure
    return primary


def random_test(asset,dates,primary,cfg,start,end):
    """Permute gaps while preserving requested holding lengths/count exactly.

    Filled counts/lengths may differ because execution constraints still apply;
    conditional subset reported separately, never labelled exact when it isn't.
    """
    closed=[t for t in primary['trades'] if t['status']=='filled' and t['side']=='sell']
    lengths=[t['holding_days'] for t in closed]
    if not lengths:return dict(status='insufficient_closed_trades',returns=[],message='没有完整往返交易，不能构造匹配随机基准')
    last_buy=next((t for t in reversed(primary['trades']) if t['status']=='filled' and t['side']=='buy'),None)
    terminal_start=dates.index(last_buy['date']) if primary['open_position'] else end
    free=terminal_start-start-sum(lengths)-len(lengths)
    if free<0:return dict(status='insufficient_space',returns=[])
    rng=random.Random(cfg.seed);returns=[];matched=[]
    for _ in range(cfg.random_runs):
        # Uniform weak composition of free days into k+1 gaps (stars and bars).
        k=len(lengths);separators=sorted(rng.sample(range(free+k),k)) if free+k>=k else list(range(k))
        points=[-1]+separators+[free+k];gaps=[points[j+1]-points[j]-1 for j in range(k+1)]
        targets=[0]*len(dates);cursor=start;shuffled=lengths[:];rng.shuffle(shuffled)
        for gap,length in zip(gaps,shuffled):
            cursor+=gap
            for index in range(cursor-1,cursor+length-1):
                if index>=0:targets[index]=1
            cursor+=length+1
        if primary['open_position']:
            for index in range(terminal_start-1,end):targets[index]=1
        result=simulate(asset,dates,targets,cfg,start,end);returns.append(result['metrics']['total_return'])
        actual=[t['holding_days'] for t in result['trades'] if t['status']=='filled' and t['side']=='sell']
        terminal_ok=result['open_position']==primary['open_position']
        if result['open_position']:
            actual_buy=next(t for t in reversed(result['trades']) if t['status']=='filled' and t['side']=='buy')
            terminal_ok=terminal_ok and actual_buy['date']==dates[terminal_start]
        if sorted(actual)==sorted(lengths) and terminal_ok:matched.append(result['metrics']['total_return'])
    score=primary['metrics']['total_return'];n=len(matched)
    return dict(status='ok' if n>=20 else 'insufficient_matched_samples',returns=returns,matched_returns=matched,requested_runs=cfg.random_runs,
                matched_runs=n,requested_round_trips=len(lengths),holding_lengths=lengths,
                percentile=sum(x<score for x in matched)/n if n else None,
                upper_tail_p=(1+sum(x>=score for x in matched))/(1+n) if n>=20 else None,
                seed=cfg.seed,message='固定交易次数与持有交易日数，随机分配空仓间隔；若有期末未平仓头寸，其起止日固定。仅对实际成交次数/持有期完全匹配的样本报告描述性排名。条件筛选与非独立收益使其不等同正式显著性证明。')


def run(bundle,cfg=None):
    cfg=(cfg or Config()).checked();validate(bundle)
    expected=digest({k:v for k,v in bundle.items() if k!='data_hash'})
    if bundle.get('data_hash')!=expected:raise ValueError('冻结数据哈希不匹配')
    symbol=bundle['symbol'];asset=bundle['assets'][symbol];benchmark=bundle['assets'][bundle['benchmark']];dates=bundle['calendar'];n=len(dates)
    # Every parameter variant uses exactly the same evaluation sessions.
    warmup=cfg.slow+2;split=warmup+int((n-warmup)*cfg.train_fraction)
    if split-warmup<252 or n-split<126:raise ValueError('有效历史不足：至少需要长均线预热、252日形成期和126日留出期')
    if any(not any(b for b in a['bars'][:warmup]) for a in (asset,benchmark)):raise ValueError('上市过晚，无法在共同预热区间开始；第一版不自动缩短样本')
    target=signals(asset['bars'],cfg.fast,cfg.slow)
    full=compare(asset,benchmark,dates,target,cfg,warmup,n)
    train=compare(asset,benchmark,dates,target,cfg,warmup,split)
    holdout=compare(asset,benchmark,dates,target,cfg,split,n)
    walk=[]
    for test_start in range(warmup+252,n,126):
        test_end=min(test_start+126,n)
        if test_end-test_start<63:continue
        fit_start=max(warmup,test_start-504)
        result=compare(asset,benchmark,dates,target,cfg,test_start,test_end)
        walk.append(dict(train_start=dates[fit_start],train_end=dates[test_start-1],test_start=dates[test_start],test_end=dates[test_end-1],
                         parameters=dict(fast=cfg.fast,slow=cfg.slow),metrics=result['metrics'],excess=result['excess'],overlaps_holdout=test_end>split))
    sensitivity=[]
    for fast in (cfg.fast-2,cfg.fast,cfg.fast+2):
        for slow in (cfg.slow-2,cfg.slow,cfg.slow+2):
            t=signals(asset['bars'],fast,slow);r=simulate(asset,dates,t,cfg,warmup,split)
            sensitivity.append(dict(fast=fast,slow=slow,metrics=r['metrics'],period='train_only'))
    center=next(r for r in sensitivity if (r['fast'],r['slow'])==(cfg.fast,cfg.slow))['metrics']['total_return']
    others=[r['metrics']['total_return'] for r in sensitivity if (r['fast'],r['slow'])!=(cfg.fast,cfg.slow)]
    random_result=random_test(asset,dates,holdout,cfg,split,n)
    gaps={s:[dates[i] for i,b in enumerate(a['bars']) if b is None] for s,a in bundle['assets'].items()}
    warnings=[
        '研究估算：公开行情缺少历史ST状态、逐日官方涨跌停价与分红送转账本，不能视为精确账户回测。',
        '当前为不含未核验分红送转的净价格收益：前复权仅用于均线信号，不复权用于实际股数、成交和估值。遗漏分红可能低估持有回报，遗漏送转可严重扭曲结果，不能视为完整投资回报。',
        '历史复权序列可能修订，冻结快照可重放，但不等同当时可获得的逐日数据版本。',
        '交易限制按当前普通板块规则假定，无法识别历史ST、临时豁免及真实排队；缺失日不成交，估值沿用上次价格并逐日标记。',
        '平均仓位匹配为事后资金暴露参照，非波动率/贝塔风险匹配，也非可预先交易的基准。现金收益默认零。',
        '留出期一旦查看，再修改参数就是再次试验，不能继续称其为未见样本。滚动窗口之间不重调参数，和留出期可能重叠，不能当独立证据相加。',
        '期末按市值估值，不强制平仓，未扣未平仓头寸的未来退出成本。历史印花税按日期变化；过户费为固定假设。',
        '当前股票池是看过近期表现后挑选的，结果只能视为个股研究，不能推广到全市场；全市场结论需要包含退市股票的历史股票池，以避免幸存者偏差。',
        '回测基于历史数据，不代表未来表现，不构成投资建议。']
    result=dict(schema=1,engine_version=ENGINE_VERSION,code_hash=code_hash(),data_hash=expected,config=asdict(cfg),symbol=symbol,name=asset['name'],
                benchmark=bundle['benchmark'],trading_profile={k:asset.get(k) for k in ('board','board_label','min_buy','lot','max_buy','limit_rate','rule_note')},source=bundle['source'],adjustment=bundle['adjustment'],data_start=dates[0],data_end=dates[-1],captured_at=bundle['captured_at'],
                evaluation_start=dates[warmup],split_date=dates[split],status='provisional_research',warnings=warnings,quality=dict(missing_dates=gaps,source=bundle['quality']),
                rule='收盘后短期SMA高于长期SMA则目标满仓，否则空仓；缺失后重新预热。只在下一交易日尝试成交，未成交按最新有效目标重试。',
                full=full,train=train,holdout=holdout,walk_forward=walk,sensitivity=sensitivity,
                sensitivity_warning=center>max(others)+.05,parameter_groups_this_run=len(sensitivity),random=random_result,
                conclusion='当前结果仅为受数据假设约束的个股研究估算，不判定策略有效；请先检查留出期相对基准、回撤、交易数量与随机匹配覆盖。')
    result['result_hash']=digest(result)
    return result
