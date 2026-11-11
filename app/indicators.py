"""Causal daily indicators. Missing sessions reset recursive indicator state.

Wilder uses a simple-mean seed, then alpha=1/n; EMA uses a simple-mean
seed, then alpha=2/(n+1). Warm-up periods remain NaN, never zero-filled.
"""
import numpy as np
import pandas as pd


def smooth(series, period, alpha=None):
    values=np.asarray(series,dtype=float);out=np.full(len(values),np.nan)
    seed=[];previous=None;alpha=1/period if alpha is None else alpha
    for i,value in enumerate(values):
        if not np.isfinite(value):seed=[];previous=None;continue
        if previous is None:
            seed.append(value)
            if len(seed)==period:previous=float(np.mean(seed));out[i]=previous
        else:
            previous=alpha*value+(1-alpha)*previous;out[i]=previous
    return pd.Series(out,index=series.index)


def calculate(frame):
    f=frame.copy();close=f['close'];previous=close.shift(1)
    delta=close.diff();gain=smooth(delta.clip(lower=0),14);loss=smooth(-delta.clip(upper=0),14)
    denominator=gain+loss
    f['rsi14']=(100*gain/denominator).where(denominator!=0,50).where(denominator.notna())
    for n in [5,10,20,60,120,250]:f[f'ma{n}']=close.rolling(n,min_periods=n).mean()
    # Align both EMA seeds at the slow EMA's first observation, as TA-Lib MACD.
    macd=pd.Series(np.nan,index=close.index)
    blocks=close.notna().ne(close.notna().shift()).cumsum()
    for _,segment in close.groupby(blocks):
        if segment.isna().any() or len(segment)<26:continue
        fast=smooth(segment.iloc[14:],12,2/13);slow=smooth(segment,26,2/27)
        macd.loc[segment.index]=fast.reindex(segment.index)-slow
    f['macd']=macd;f['macd_signal']=smooth(f['macd'],9,2/10)
    # One times DIF-DEA; some Chinese software displays two times this value.
    f['macd_hist']=f['macd']-f['macd_signal']
    valid=f[['high','low','close']].notna().all(axis=1)&previous.notna()
    tr=pd.concat([f['high']-f['low'],(f['high']-previous).abs(),(f['low']-previous).abs()],axis=1).max(axis=1).where(valid)
    f['atr14']=smooth(tr,14);f['atr_pct']=f['atr14']/close*100
    mid=f['ma20'];std=close.rolling(20,min_periods=20).std(ddof=0)
    f['boll_upper']=mid+2*std;f['boll_lower']=mid-2*std
    f['boll_position']=(close-f['boll_lower'])/(f['boll_upper']-f['boll_lower'])
    f['volume_ratio20']=f['volume']/f['volume'].shift(1).rolling(20,min_periods=20).mean().replace(0,np.nan)
    for n in [20,60]:
        f[f'prior_high{n}']=f['high'].shift(1).rolling(n,min_periods=n).max()
        f[f'prior_low{n}']=f['low'].shift(1).rolling(n,min_periods=n).min()
    f['bias20_pct']=(close/f['ma20']-1)*100
    f['drawdown252_pct']=(close/close.rolling(252,min_periods=252).max()-1)*100
    f['realized_vol20_pct']=np.log(close).diff().rolling(20,min_periods=20).std(ddof=1)*np.sqrt(252)*100
    return f


def events(frame):
    """Signals use information observable at the current close, no future prices."""
    diff=frame['ma20']-frame['ma60']
    break_above=frame['close']>frame['prior_high20']
    return {
        'ma_cross':(diff>0)&(diff.shift(1)<=0),
        'breakout_volume':break_above&~break_above.shift(1,fill_value=False)&(frame['volume_ratio20']>=1.5),
        'rsi_enter70':(frame['rsi14']>=70)&(frame['rsi14'].shift(1)<70),
    }


def spaced_events(signal,horizon=20):
    """Deterministically retain the first event, then block overlapping horizons."""
    selected=[];last=-horizon
    for position,flag in enumerate(signal.fillna(False)):
        if flag and position-last>=horizon:selected.append(position);last=position
    return selected
