"""Immutable, same-provider research inputs; never use UI/quote prices."""
import hashlib
import json
import math
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from . import core, technical, tencent_source


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def validate(bundle):
    dates=bundle['calendar']
    if len(dates)!=len(set(dates)) or dates!=sorted(dates):raise ValueError('交易日历重复或未排序')
    for symbol,asset in bundle['assets'].items():
        if len(asset['bars'])!=len(dates):raise ValueError('行情与交易日历长度不一致')
        for day,event in asset.get('corporate_actions',{}).items():
            if day not in dates or not asset.get('corporate_actions_verified'):raise ValueError('企业行动日期或核验状态错误')
            if not math.isfinite(event.get('split_ratio',1)) or event.get('split_ratio',1)<=0:raise ValueError('送转比例错误')
            if not math.isfinite(event.get('cash_per_share',0)) or event.get('cash_per_share',0)<0:raise ValueError('分红金额错误')
            if event.get('payment_date',day)<day:raise ValueError('分红支付日早于权益确认日')
        for day,bar in zip(dates,asset['bars']):
            if bar is None:continue
            if bar['date']!=day:raise ValueError('行情与交易日期不一致')
            for name in ('open','high','low','close','adjusted_close'):
                if not math.isfinite(bar[name]) or bar[name]<=0:raise ValueError('非有限或非正行情')
            if not bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']:raise ValueError('OHLC 顺序错误')
            if not math.isfinite(bar['volume']) or bar['volume']<0:raise ValueError('成交量错误')
    return bundle


def profile(symbol,name,kind=None,allow_research=False):
    code,exchange=symbol.split('.')
    is_etf=kind=='etf' or (kind is None and (code.startswith(('51','52','56','58','15','16'))))
    risk=bool(re.match(r'^(?:S\*?ST|\*?ST)',name.upper())) or '退' in name
    board='etf' if is_etf else 'beijing' if exchange=='BJ' else 'star' if exchange=='SH' and code.startswith(('688','689')) else 'chinext' if exchange=='SZ' and code.startswith(('300','301','302')) else 'main' if (exchange=='SH' and code.startswith(('600','601','603','605')) or exchange=='SZ' and code.startswith(('000','001','002','003'))) else 'unknown'
    labels={'etf':'ETF','beijing':'北交所','star':'科创板','chinext':'创业板','main':'沪深主板','unknown':'其他证券'}
    reason=''
    if board=='unknown':reason='尚未核验该证券的交易制度，仅提供行情与指标研究'
    elif risk:reason='缺历史ST/退市状态与逐日涨跌停价，可做技术观察，暂不开放回测'
    elif is_etf and symbol!='510300.SH':reason='ETF含不同涨跌幅、回转与资产类别，当前仅510300的回测规则已接入'
    if reason and not allow_research:raise ValueError(reason)
    return dict(kind='etf' if is_etf else 'stock',board=board,board_label=labels[board],risk_warning=risk,
                limit_rate=None if is_etf and symbol!='510300.SH' else .30 if board=='beijing' else .20 if board in ('star','chinext') else .10,
                lot=1 if board in ('star','beijing') else 100,min_buy=200 if board=='star' else 100,
                max_buy=100000 if board=='star' else 1000000,tick=.001 if is_etf else .01,
                backtest_allowed=not bool(reason),backtest_reason=reason,
                rule_note='普通交易日板块假设；历史ST、首发/重新上市豁免及企业行动未核验')


def acquire(symbol):
    names=technical.resolve_watchlist([symbol]);symbol=next(iter(names));profile(symbol,names[symbol])
    assets=dict(names,**{technical.BENCHMARK:'沪深300ETF'})
    now=core.now().astimezone(core.SH)
    end=(now.date() if now.hour>=16 else now.date()-timedelta(days=1)).isoformat()
    start=core.history_start();calendar=[d for d in core.calendar_dates() if start<=d<=end]
    if not calendar:raise ValueError('交易日历不可用')
    with tempfile.TemporaryDirectory() as temp:
        directory=Path(temp)
        with ThreadPoolExecutor(max_workers=2) as pool:
            sources=list(pool.map(lambda spec:tencent_source.fetch_one(spec,directory,start,calendar[-1]),[(s,a) for s in assets for a in (0,1,2)]))
        for source in sources:
            if source.get('source_name'):assets[source['symbol']]=source['source_name']
        manifest=dict(calendar=calendar,assets=assets,window_start=start,expected_end=calendar[-1],sources=sources)
        frames,quality=technical.load_frames(directory,manifest)
        dates=list(next(iter(frames.values())).index)
        result=dict(schema=1,symbol=symbol,benchmark=technical.BENCHMARK,calendar=dates,assets={},source='腾讯证券公开日线',adjustment='前复权均线信号；不复权成交与净价格收益（不含未核验分红送转）',quality=quality,
                    captured_at=core.iso(),requested_end=calendar[-1],evidence={s['file']:(directory/s['file']).read_text(encoding='utf-8') for s in sources},sources=sources)
        for key,name in assets.items():
            raw=frames[(key,0)];adj=frames[(key,1)];bars=[]
            for day in dates:
                r=raw.loc[day]
                if not math.isfinite(float(r['close'])):bars.append(None);continue
                bars.append(dict(date=day,**{k:float(r[k]) for k in ('open','high','low','close','volume')},adjusted_close=float(adj.loc[day,'close'])))
            result['assets'][key]=dict(name=name,**profile(key,name),bars=bars,corporate_actions={},corporate_actions_verified=False)
    validate(result)
    result['data_hash']=digest({k:v for k,v in result.items() if k!='data_hash'})
    return result
