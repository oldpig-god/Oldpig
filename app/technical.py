"""Selectable A-share/ETF technical research. CLI and API share this reproducible pipeline."""
import argparse
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd
import requests

from . import core, indicators, process_lock

WATCHLIST={'002463.SZ':'沪电股份','002384.SZ':'东山精密','603256.SH':'宏和科技',
           '002938.SZ':'鹏鼎控股','002484.SZ':'江海股份','600031.SH':'三一重工'}
BENCHMARK='510300.SH'
ASSETS=dict(WATCHLIST,**{BENCHMARK:'沪深300ETF'})
PUBLIC=core.ROOT/'app/static/technical'
PORTABLE=core.ROOT/'research/technical/snapshot'
ENDPOINT='https://push2his.eastmoney.com/api/qt/stock/kline/get'
RULES={'ma_cross':'MA20上穿MA60','breakout_volume':'突破前20日高点且量比≥1.5','rsi_enter70':'RSI首次进入70以上'}
METHODS={
    'technical_price':'前复权OHLC乘固定比例，锚定样本末日不复权收盘价；历史参考价不等于历史实际成交价',
    'return_price':'后复权价格收益代理；未独立核验为分红再投资总回报',
    'missing':'按交易日历留空；不填价，不跨缺失日计算日收益，递推指标遇缺失重置',
    'warmup':'不足完整窗口显示null；首250个交易日用于长均线预热，事件形成期从其后开始；RSI与ATR用Wilder均值种子，RSI完全横盘为50；MACD快慢EMA种子在慢线首日对齐',
    'quote':'快照单独保存源时点与抓取时点；价格按源f59小数位缩放，涨幅由源昨收计算；不拼入完整日线',
    'macd':'EMA12−EMA26，信号EMA9；柱为DIF−DEA，非部分国内软件的2倍柱',
    'volume':'当日原始量÷此前20个交易日平均原始量；只在同证券同来源比较，不比较未核验绝对量单位',
    'levels':'前20/60日最高与最低，不含当日；MA和ATR仅作观察参考，不视为确定支撑/止损或下单指令',
    'event_study':'固定3个规则，不调参；前80%日期为形成期，后20%为留出期；按证券/规则每20日最多保留一次事件',
    'event_outcome':'信号收盘至20个交易日后收盘的后复权简单收益减同期ETF收益；缺任一持有期交易日则排除；事件观察统计，不含执行、成本、涨跌停或T+1约束',
    'bias':'用户按当前截图选取的6只存续股票，存在选择偏差；复权历史可修订，非严格时点回测；组间事件也可能受共同市场因素影响',
}
REFERENCES=[
 {'name':'TA-Lib：函数分类与参考数据','url':'https://ta-lib.org/functions/'},
 {'name':'ta：pandas/NumPy技术指标框架（方法参考）','url':'https://github.com/bukosabino/ta'},
 {'name':'Fidelity：RSI与趋势中的超买区','url':'https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI'},
 {'name':'Fidelity：ATR度量波动而非方向','url':'https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr'},
]


def resolve_watchlist(queries):
    if not 1<=len(queries)<=12:raise ValueError('每次请选择1至12只证券')
    result={}
    for query in queries:
        query=query.strip().upper()
        with core.db() as connection:
            rows=connection.execute("SELECT symbol,name FROM instruments WHERE active=1 AND (symbol=? OR code=? OR name=?)",(query,query,query)).fetchall()
        if len(rows)==1:
            result[rows[0]['symbol']]=rows[0]['name'];continue
        if len(rows)>1:raise ValueError('名称存在歧义，请使用完整证券代码：'+query)
        if re.fullmatch(r'\d{6}',query):
            exchange='SH' if query[0] in '56' else 'BJ' if query[0] in '489' else 'SZ'
            query+='.'+exchange
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',query):raise ValueError('未找到证券，请输入六位代码或完整名称：'+query)
        result[query]=query
    return result


def fetch_one(spec,directory,start,end):
    symbol,adjustment=spec;code,exchange=symbol.split('.');market=1 if exchange=='SH' else 0
    params=dict(secid=f'{market}.{code}',klt='101',fqt=str(adjustment),beg=start.replace('-',''),end=end.replace('-',''),
                fields1='f1,f2,f3,f4,f5,f6',fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
    response=requests.get(ENDPOINT,params=params,timeout=(5,30));response.raise_for_status();payload=response.json();data=payload.get('data')
    if payload.get('rc',0) or not data or data.get('code')!=code or data.get('market')!=market:
        raise ValueError('Unexpected source identity: '+symbol)
    if not data.get('klines'):raise ValueError('Empty history: '+symbol)
    filename=f'{symbol}.{adjustment}.json';(directory/filename).write_bytes(response.content)
    return dict(symbol=symbol,adjustment=adjustment,file=filename,sha256=hashlib.sha256(response.content).hexdigest(),
                endpoint=ENDPOINT,parameters=params,fetched_at=core.iso(),source_name=data.get('name'))


def quote_snapshot(symbol,directory):
    code,exchange=symbol.split('.');params={'secid':('1' if exchange=='SH' else '0')+'.'+code,'fields':'f43,f57,f58,f59,f60,f86'}
    endpoint='https://push2.eastmoney.com/api/qt/stock/get';fetched=core.iso()
    try:
        response=requests.get(endpoint,params=params,timeout=(5,15));response.raise_for_status();payload=response.json();data=payload.get('data')
        if payload.get('rc',0) or not data or data['f57']!=code:raise ValueError('Quote identity mismatch')
        decimals=int(data['f59'])
        if not 0<=decimals<=6:raise ValueError('Quote precision invalid')
        price=float(data['f43'])/10**decimals;previous=float(data['f60'])/10**decimals
        stamp=datetime.fromtimestamp(int(data['f86']),core.timezone.utc)
        if min(price,previous)<=0 or stamp>core.now()+timedelta(minutes=5):raise ValueError('Invalid quote/time')
        filename=f'{symbol}.spot.json';(directory/filename).write_bytes(response.content)
        return dict(symbol=symbol,status='ok',price=price,previous_close=previous,pct_change=(price/previous-1)*100,
                    event_time=stamp.astimezone(core.SH).isoformat(),fetched_at=fetched,endpoint=endpoint,parameters=params,
                    file=filename,sha256=hashlib.sha256(response.content).hexdigest())
    except Exception as exc:return dict(symbol=symbol,status='error',error=str(exc)[:300],fetched_at=fetched)


def attach_quotes(directory):
    path=directory/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    with ThreadPoolExecutor(max_workers=2) as pool:manifest['quotes']=list(pool.map(lambda s:quote_snapshot(s,directory),manifest['watchlist']))
    path.write_text(core.dumps(manifest),encoding='utf-8')


def download_snapshot(watchlist=None):
    watchlist=dict(watchlist or WATCHLIST)
    assets=dict(watchlist,**{BENCHMARK:'沪深300ETF'})
    local=core.now().astimezone(core.SH);start=core.history_start()
    bound=local.date() if local.hour>=16 else local.date()-timedelta(days=1)
    days=[d for d in core.calendar_dates() if start<=d<=bound.isoformat()]
    if not days:raise ValueError('Trading calendar unavailable; refresh saved calendar first')
    directory=core.DATA/'research/technical'/(local.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]);directory.mkdir(parents=True,exist_ok=False)
    specs=[(s,a) for s in assets for a in [0,1,2]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        sources=list(pool.map(lambda spec:fetch_one(spec,directory,start,days[-1]),specs))
    # Trust validated source identity for the display name, including code-only input.
    for source in sources:
        if source.get('source_name'):
            assets[source['symbol']]=source['source_name']
            if source['symbol'] in watchlist:watchlist[source['symbol']]=source['source_name']
    manifest=dict(schema_version=1,generated_at=core.iso(),window_start=start,expected_end=days[-1],calendar=days,
                  assets=assets,watchlist=watchlist,benchmark=BENCHMARK,sources=sources)
    (directory/'manifest.json').write_text(core.dumps(manifest),encoding='utf-8')
    attach_quotes(directory)
    return directory


def load_frames(directory,manifest):
    frames={};quality=[];calendar=manifest['calendar'];session_set=set(calendar)
    for source in manifest['sources']:
        content=(directory/source['file']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=source['sha256']:raise ValueError('Evidence checksum mismatch: '+source['file'])
        payload=json.loads(content);data=payload['data'];symbol=source['symbol'];code,exchange=symbol.split('.')
        if str(data.get('code'))!=code or data.get('market')!=(1 if exchange=='SH' else 0):raise ValueError('Snapshot identity mismatch')
        rows=[];seen=set()
        for line in data['klines']:
            cells=line.split(',')
            if len(cells)!=11:raise ValueError('Kline schema mismatch')
            date=cells[0]
            if date not in session_set or date in seen:raise ValueError('Unexpected/duplicate trading date: '+date)
            seen.add(date);o,c,h,l,v,amount=map(float,cells[1:7])
            if not np.isfinite([o,c,h,l,v,amount]).all() or min(o,c,h,l)<=0 or min(v,amount)<0 or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError('Invalid OHLCV: '+symbol+' '+date)
            rows.append(dict(date=date,open=o,close=c,high=h,low=l,volume=v,amount=amount,pct_source=float(cells[8])))
        frame=pd.DataFrame(rows).set_index('date').reindex(calendar)
        frames[(symbol,int(source['adjustment']))]=frame
        quality.append(dict(symbol=symbol,adjustment=source['adjustment'],rows=len(rows),missing_sessions=len(calendar)-len(rows),
                            first=min(seen),last=max(seen)))
    for symbol in manifest['assets']:
        raw=frames[(symbol,0)]
        for adjustment in [1,2]:
            adjusted=frames[(symbol,adjustment)]
            if not raw.index[raw['close'].notna()].equals(adjusted.index[adjusted['close'].notna()]):raise ValueError('Adjustment date mismatch: '+symbol)
            if not np.allclose(raw['volume'],adjusted['volume'],equal_nan=True):raise ValueError('Adjustment volume mismatch: '+symbol)
    for quote in manifest.get('quotes',[]):
        if quote['status']!='ok':continue
        content=(directory/quote['file']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=quote['sha256']:raise ValueError('Quote evidence checksum mismatch')
        data=json.loads(content)['data'];scale=10**int(data['f59'])
        if data['f57']!=quote['symbol'].split('.')[0] or float(data['f43'])/scale!=quote['price']:
            raise ValueError('Quote snapshot identity/value mismatch')
        source_time=datetime.fromtimestamp(int(data['f86']),core.timezone.utc)
        if source_time!=datetime.fromisoformat(quote['event_time']):raise ValueError('Quote source time mismatch')
    return frames,quality


def clean_numbers(value):
    if isinstance(value,dict):return {k:clean_numbers(v) for k,v in value.items()}
    if isinstance(value,list):return [clean_numbers(v) for v in value]
    if isinstance(value,float) and not np.isfinite(value):return None
    return value


def finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def outcome(price,benchmark,position,horizon=20):
    a=price.iloc[position:position+horizon+1];b=benchmark.iloc[position:position+horizon+1]
    if len(a)!=horizon+1 or a.isna().any() or b.isna().any():return None
    stock=float(a.iloc[-1]/a.iloc[0]-1);market=float(b.iloc[-1]/b.iloc[0]-1)
    return dict(stock_pct=stock*100,benchmark_pct=market*100,excess_pct=(stock-market)*100)


def event_study(frame,price,benchmark,split):
    horizon=20;rules=indicators.events(frame);stats=[];observations=[]
    for period,begin,end in [('train',250,split),('holdout',split,len(frame))]:
        baseline=[r for i in range(begin,end-horizon) if (r:=outcome(price,benchmark,i)) is not None]
        base_mean=float(np.mean([r['excess_pct'] for r in baseline])) if baseline else None
        for rule,flags in rules.items():
            positions=[begin+i for i in indicators.spaced_events(flags.iloc[begin:end],horizon)]
            results=[];pending=missing=0
            for i in positions:
                if i+horizon>=end:pending+=1;continue
                result=outcome(price,benchmark,i)
                if result is None:missing+=1;continue
                results.append(result);observations.append(dict(rule=rule,period=period,date=frame.index[i],end_date=frame.index[i+horizon],**result))
            xs=[r['excess_pct'] for r in results]
            stats.append(dict(period=period,rule=rule,label=RULES[rule],n=len(xs),
                mean_excess_pct=float(np.mean(xs)) if xs else None,median_excess_pct=float(np.median(xs)) if xs else None,
                q25_excess_pct=float(np.quantile(xs,.25)) if xs else None,q75_excess_pct=float(np.quantile(xs,.75)) if xs else None,
                outperform_rate=float(np.mean(np.array(xs)>0)) if xs else None,
                mean_stock_pct=float(np.mean([r['stock_pct'] for r in results])) if xs else None,
                baseline_mean_excess_pct=base_mean,baseline_n=len(baseline),pending_or_boundary= pending,missing_outcomes=missing,
                low_sample=len(xs)<10))
    return stats,observations


def summarize(symbol,frame,raw,return_price,benchmark):
    date=raw['close'].last_valid_index();row=frame.loc[date];price=float(raw.loc[date,'close'])
    snapshot={k:finite(v) for k,v in row.items()}
    snapshot.update(raw_close=price,raw_pct_change=finite(raw.loc[date,'pct_source']),date=date)
    for n in [5,20,63,252]:
        index=return_price.index.get_loc(date)
        r=outcome(return_price,benchmark,index-n,n) if index>=n else None
        snapshot[f'return{n}_pct']=r['stock_pct'] if r else None
        snapshot[f'excess{n}_pct']=r['excess_pct'] if r else None
    if row[['ma20','ma60']].notna().all():
        trend='中期上行' if row['close']>row['ma60'] and row['ma20']>row['ma60'] else '中期偏弱' if row['close']<row['ma60'] and row['ma20']<row['ma60'] else '趋势分歧'
    else:trend='历史不足'
    notes=[]
    if pd.notna(row['bias20_pct']):notes.append(f"收盘价较MA20偏离{row['bias20_pct']:+.1f}%")
    if pd.notna(row['rsi14']):notes.append('RSI进入70以上，强势也可能持续，不能据此单独判断反转' if row['rsi14']>=70 else 'RSI低于30，弱势也可能延续' if row['rsi14']<30 else 'RSI位于30至70之间')
    if pd.notna(row['volume_ratio20']):notes.append(f"当日量为此前20日均量的{row['volume_ratio20']:.2f}倍")
    if pd.notna(row['prior_high20']):notes.append('收盘突破前20日最高价' if row['close']>row['prior_high20'] else f"距前20日高点{(row['close']/row['prior_high20']-1)*100:.1f}%")
    return dict(symbol=symbol,name=symbol,latest=snapshot,trend=trend,observations=len(raw.dropna()),notes=notes)


def analyze_snapshot(directory):
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'));frames,quality=load_frames(directory,manifest)
    watchlist={s['symbol']:manifest['watchlist'][s['symbol']] for s in manifest['sources'] if s['symbol'] in manifest['watchlist']}
    benchmark_symbol=manifest['benchmark']
    days=manifest['calendar'];split=int(len(days)*.8);benchmark=frames[(benchmark_symbol,2)]['close']
    assets=[];series={};event_rows=[];event_stats=[];returns={};qfq_returns={}
    for symbol in watchlist:
        raw=frames[(symbol,0)];qfq=frames[(symbol,1)].copy();date=raw['close'].last_valid_index()
        factor=float(raw.loc[date,'close']/qfq.loc[date,'close'])
        qfq[['open','high','low','close']]*=factor
        frame=indicators.calculate(qfq);return_price=frames[(symbol,2)]['close']
        assets.append(summarize(symbol,frame,raw,return_price,benchmark))
        assets[-1]['anchor_factor']=factor
        assets[-1]['name']=watchlist[symbol]
        stats,events=event_study(frame,return_price,benchmark,split)
        event_stats.extend(dict(symbol=symbol,name=watchlist[symbol],**r) for r in stats)
        event_rows.extend(dict(symbol=symbol,name=watchlist[symbol],**r) for r in events)
        chart=frame.copy();chart['raw_close']=raw['close'];chart['return_hfq']=np.log(return_price).diff()
        chart['date']=chart.index
        series[symbol]=json.loads(chart.to_json(orient='records',force_ascii=False))
        returns[symbol]=np.log(return_price).diff();qfq_returns[symbol]=np.log(qfq['close']).diff()
    returns[benchmark_symbol]=np.log(benchmark).diff()
    aligned=pd.DataFrame(returns).dropna();stocks=aligned[list(watchlist)]
    x=np.column_stack([np.ones(len(aligned)),aligned[benchmark_symbol]])
    residual=stocks.to_numpy()-x@np.linalg.lstsq(x,stocks.to_numpy(),rcond=None)[0]
    # A benchmark regressed on itself has no residual variation.
    if len(residual):residual[:,np.max(np.abs(residual),axis=0)<1e-12]=np.nan
    correlation=stocks.corr();recent=stocks.tail(60).corr();residual_corr=pd.DataFrame(residual,columns=watchlist).corr()
    qcorr=pd.DataFrame(qfq_returns).reindex(aligned.index).corr()
    pairs=[]
    symbols=list(watchlist)
    for i,a in enumerate(symbols):
        for b in symbols[i+1:]:pairs.append(dict(a=a,b=b,names=watchlist[a]+' / '+watchlist[b],
            correlation=float(correlation.loc[a,b]),recent60=float(recent.loc[a,b]),residual=float(residual_corr.loc[a,b]),
            qfq_correlation=float(qcorr.loc[a,b])))
    pairs.sort(key=lambda r:r['recent60'],reverse=True)
    qfq_delta=float(np.max(np.abs(correlation.to_numpy()-qcorr.to_numpy())))
    actual_end=min(q['last'] for q in quality if q['adjustment']==0)
    result=dict(schema_version=1,generated_at=core.iso(),source_fetched_at=manifest['generated_at'],
        window_start=manifest['window_start'],expected_end=manifest['expected_end'],daily_asof=actual_end,watchlist=watchlist,benchmark=benchmark_symbol,
        assets=assets,series=series,quality=quality,methods=dict(METHODS,bias='当前用户选择的证券存在选择与存续偏差；复权历史可修订，非严格时点回测；组间事件可能受共同市场因素影响'),references=REFERENCES,
        dates=dict(train_start=days[250],train_end=days[split-1],holdout_start=days[split],holdout_end=days[-1],
                   calendar_sessions=len(days),return_start=aligned.index[0] if len(aligned) else None,return_end=aligned.index[-1] if len(aligned) else None,
                   return_observations=len(aligned),recent60_start=aligned.tail(60).index[0] if len(aligned) else None),
        event_statistics=event_stats,events=event_rows,
        comovement=dict(correlation=correlation.to_dict(orient='index'),recent60=recent.to_dict(orient='index'),
            residual=residual_corr.to_dict(orient='index'),sample_cov_daily=(stocks.cov() if len(stocks)>1 else pd.DataFrame(np.nan,index=stocks.columns,columns=stocks.columns)).to_dict(orient='index'),
            pairs=pairs,qfq_hfq_max_correlation_delta=qfq_delta),
        quotes=manifest.get('quotes',[]),provenance=manifest,evidence_path='evidence.zip')
    # Undefined correlations (short/constant/disjoint histories) are null, never NaN.
    return clean_numbers(result)


def publish(result,directory,output=PUBLIC,portable=True):
    output.mkdir(parents=True,exist_ok=True)
    if portable and directory.resolve()!=PORTABLE.resolve():
        PORTABLE.mkdir(parents=True,exist_ok=True)
        for source in result['provenance']['sources']+result['provenance'].get('quotes',[]):
            if source.get('file'):shutil.copyfile(directory/source['file'],PORTABLE/source['file'])
        shutil.copyfile(directory/'manifest.json',PORTABLE/'manifest.json')
    with zipfile.ZipFile(output/'evidence.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory/'manifest.json','snapshot/manifest.json')
        for source in result['provenance']['sources']+result['provenance'].get('quotes',[]):
            if source.get('file'):archive.write(directory/source['file'],'snapshot/'+source['file'])
    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    pd.DataFrame([dict(symbol=a['symbol'],name=a['name'],trend=a['trend'],**a['latest']) for a in result['assets']]).to_csv(output/'summary.csv',index=False)
    pd.DataFrame(result['event_statistics']).to_csv(output/'event_statistics.csv',index=False)
    pd.DataFrame(result['events']).to_csv(output/'events.csv',index=False)
    for key in ['correlation','recent60','residual','sample_cov_daily']:
        pd.DataFrame.from_dict(result['comovement'][key],orient='index').to_csv(output/(key+'.csv'),index_label='symbol')
    # Commit the visible result last. Windows may deny a CSV write while Excel has
    # the destination open; the preceding analysis must remain available on failure.
    temp=output/'analysis.json.tmp';temp.write_text(encoded,encoding='utf-8');temp.replace(output/'analysis.json')


def build(snapshot=None,output=PUBLIC,watchlist=None):
    core.DATA.mkdir(parents=True,exist_ok=True)
    with (core.DATA/'technical.lock').open('a+b') as lock:
        process_lock.acquire(lock)
        directory=Path(snapshot) if snapshot else download_snapshot(watchlist)
        result=analyze_snapshot(directory);publish(result,directory,output,portable=watchlist is None)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot',type=Path);parser.add_argument('--output',type=Path,default=PUBLIC)
    parser.add_argument('--symbols',nargs='+',help='1-12个A股/ETF代码或完整名称；与--snapshot互斥')
    args=parser.parse_args()
    if args.snapshot and args.symbols:parser.error('--snapshot与--symbols不能同时使用')
    if args.symbols:core.init()
    result=build(args.snapshot,args.output,resolve_watchlist(args.symbols) if args.symbols else None)
    print(json.dumps(dict(dates=result['dates'],assets=[dict(symbol=a['symbol'],name=a['name'],trend=a['trend'],latest=a['latest']) for a in result['assets']]),ensure_ascii=False,indent=2))
