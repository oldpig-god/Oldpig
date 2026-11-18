"""Causal daily indicators. Missing sessions reset recursive indicator state.

Wilder uses a simple-mean seed, then alpha=1/n; EMA uses a simple-mean
seed, then alpha=2/(n+1). Warm-up periods remain NaN, never zero-filled.
"""
import numpy as np
import pandas as pd

MA_PERIODS = (5, 10, 20, 60, 120, 250)
EMA_PERIODS = (5, 10, 12, 20, 26, 50, 60, 120, 250)


def _clean_float(value, digits=6):
    if value is None:
        return None
    f = float(value)
    return round(f, digits) if np.isfinite(f) else None


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


def _macd_components(close):
    macd=pd.Series(np.nan,index=close.index)
    blocks=close.notna().ne(close.notna().shift()).cumsum()
    for _,segment in close.groupby(blocks):
        if segment.isna().any() or len(segment)<26:continue
        fast=smooth(segment.iloc[14:],12,2/13);slow=smooth(segment,26,2/27)
        macd.loc[segment.index]=fast.reindex(segment.index)-slow
    signal=smooth(macd,9,2/10)
    # One times DIF-DEA; some Chinese software displays two times this value.
    hist=macd-signal
    return macd,signal,hist


def calculate(frame, continuous=False):
    f=frame.copy();close=f['close'];previous=close.shift(1)
    delta=close.diff();gain=smooth(delta.clip(lower=0),14);loss=smooth(-delta.clip(upper=0),14)
    denominator=gain+loss
    f['rsi14']=(100*gain/denominator).where(denominator!=0,50).where(denominator.notna())
    for n in MA_PERIODS:f[f'ma{n}']=close.rolling(n,min_periods=n).mean()
    for n in EMA_PERIODS:f[f'ema{n}']=smooth(close,n,2/(n+1))
    # Align both EMA seeds at the slow EMA's first observation, as TA-Lib MACD.
    f['macd'],f['macd_signal'],f['macd_hist']=_macd_components(close)

    # Session-continuous MA / EMA / MACD: skip missing/suspended sessions so indicators stay unbroken across suspensions.
    valid_close=close.dropna()
    if len(valid_close):
        for n in MA_PERIODS:f[f'c_ma{n}']=valid_close.rolling(n,min_periods=n).mean().reindex(f.index)
        for n in EMA_PERIODS:f[f'c_ema{n}']=smooth(valid_close,n,2/(n+1)).reindex(f.index)
        cm,cs,ch=_macd_components(valid_close)
        f['c_macd']=cm.reindex(f.index);f['c_macd_signal']=cs.reindex(f.index);f['c_macd_hist']=ch.reindex(f.index)
    else:
        for n in MA_PERIODS:f[f'c_ma{n}']=np.nan
        for n in EMA_PERIODS:f[f'c_ema{n}']=np.nan
        f['c_macd']=np.nan;f['c_macd_signal']=np.nan;f['c_macd_hist']=np.nan

    if continuous:
        for n in MA_PERIODS:f[f'ma{n}']=f[f'c_ma{n}']
        for n in EMA_PERIODS:f[f'ema{n}']=f[f'c_ema{n}']
        f['macd']=f['c_macd'];f['macd_signal']=f['c_macd_signal'];f['macd_hist']=f['c_macd_hist']

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


def series_indicators(records):
    """Compute continuous MA, EMA and MACD series on valid bars for /api/series responses."""
    if not records:
        return {'latest': None, 'continuous': True, 'periods': {'ma': list(MA_PERIODS), 'ema': list(EMA_PERIODS), 'macd': [12, 26, 9]}}
    closes=pd.Series([float(r['close']) if r.get('close') is not None else np.nan for r in records],dtype=float)
    valid=closes.dropna()
    cols={}
    if len(valid):
        for n in MA_PERIODS:cols[f'ma{n}']=valid.rolling(n,min_periods=n).mean().reindex(closes.index)
        for n in EMA_PERIODS:cols[f'ema{n}']=smooth(valid,n,2/(n+1)).reindex(closes.index)
        m,s,h=_macd_components(valid)
        cols['macd']=m.reindex(closes.index);cols['macd_signal']=s.reindex(closes.index);cols['macd_hist']=h.reindex(closes.index)
    else:
        for n in MA_PERIODS:cols[f'ma{n}']=pd.Series(np.nan,index=closes.index)
        for n in EMA_PERIODS:cols[f'ema{n}']=pd.Series(np.nan,index=closes.index)
        cols['macd']=cols['macd_signal']=cols['macd_hist']=pd.Series(np.nan,index=closes.index)

    attach_keys=('ma5','ma10','ma20','ma60','ema12','ema20','ema26','ema50','ema60','macd','macd_signal','macd_hist')
    for idx,rec in enumerate(records):
        for k in attach_keys:
            rec[k]=_clean_float(cols[k].iloc[idx])

    last_idx=valid.index[-1] if len(valid) else None
    latest=None
    if last_idx is not None:
        latest={k:_clean_float(s.iloc[last_idx]) for k,s in cols.items()}
        latest['event_time']=records[last_idx].get('event_time')
        latest['close']=_clean_float(records[last_idx].get('close'))
    return {'latest': latest, 'continuous': True, 'periods': {'ma': list(MA_PERIODS), 'ema': list(EMA_PERIODS), 'macd': [12, 26, 9]}}


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
