"""Reusable extraction of MA/MACD/Wilder RSI/drawdown from the verified case."""
import argparse,json
from pathlib import Path
import pandas as pd
import numpy as np

def indicators(df):
    d=df.copy().sort_values('date')
    if not d.date.is_unique or d.close.isna().any() or (d.close<=0).any():raise ValueError('Invalid price series')
    for n in [20,60,120,250]:d[f'MA{n}']=d.close.rolling(n).mean()
    d['DIF']=d.close.ewm(span=12,adjust=False).mean()-d.close.ewm(span=26,adjust=False).mean()
    d['DEA']=d.DIF.ewm(span=9,adjust=False).mean();d['MACD']=2*(d.DIF-d.DEA)
    change=d.close.diff();gain=change.clip(lower=0);loss=(-change).clip(lower=0)
    rsi=np.full(len(d),np.nan)
    if len(d)>14:
        g=float(gain.iloc[1:15].mean());l=float(loss.iloc[1:15].mean())
        for i in range(14,len(d)):
            if i>14:g=(g*13+gain.iloc[i])/14;l=(l*13+loss.iloc[i])/14
            rsi[i]=50 if g==l==0 else 100 if l==0 else 100-100/(1+g/l)
    d['RSI14']=rsi;d['return_price']=d.close.pct_change(fill_method=None);d['drawdown']=d.close/d.close.cummax()-1
    return d
def correlation(left,right):
    a=left[['date','close']].copy();b=right[['date','close']].copy()
    if not a.date.is_unique or not b.date.is_unique:raise ValueError('Duplicate dates')
    a=a.set_index('date').sort_index();b=b.set_index('date').sort_index()
    aa=pd.DataFrame({'left':a.close.pct_change(fill_method=None),'previous_left':a.index.to_series().shift()})
    bb=pd.DataFrame({'right':b.close.pct_change(fill_method=None),'previous_right':b.index.to_series().shift()})
    joined=aa.join(bb,how='inner').dropna()
    joined=joined[joined.previous_left==joined.previous_right]
    return {'observations':len(joined),'pearson_daily_return':float(joined.left.corr(joined.right)) if len(joined)>=3 and joined.left.std()>0 and joined.right.std()>0 else None}

def run(bundle,other_bundle=None):
    b=Path(bundle);meta=json.loads((b/'bundle.json').read_text(encoding='utf-8'));q=b/'quant'
    q.mkdir(exist_ok=False)
    d=indicators(pd.read_csv(b/'price_qfq.csv',parse_dates=['date']))
    d.to_csv(q/'indicators.csv',index=False,encoding='utf-8-sig')
    result={'symbol':meta['symbol'],'basis':'Eastmoney qfq price returns; not total return','rows':len(d),'last_date':str(d.date.iloc[-1].date()),'max_drawdown':float(d.drawdown.min()),'volatility_250d':float(d.return_price.tail(250).std(ddof=1)*np.sqrt(250)) if len(d)>=251 else None,'latest':{k:float(d.iloc[-1][k]) if pd.notna(d.iloc[-1][k]) else None for k in ['MA20','MA60','MA120','MA250','DIF','DEA','MACD','RSI14']},'backtest_executed':False}
    result['start_date']=str(d.date.iloc[0].date())
    if other_bundle:
        other=pd.read_csv(Path(other_bundle)/'price_qfq.csv',parse_dates=['date'])
        result['correlation']=correlation(d,other)
    (q/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--other-bundle');a=p.parse_args();print(json.dumps(run(a.bundle,a.other_bundle),ensure_ascii=False))
