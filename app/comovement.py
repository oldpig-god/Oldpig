"""Reproducible, read-only research on a fixed, explicitly selected universe.

Run: python3 -m app.comovement [--snapshot existing-directory]
Primary unadjusted observations are never modified by this pipeline.
"""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.cluster.hierarchy import linkage, leaves_list, fcluster
from scipy.spatial.distance import squareform
from sklearn.covariance import LedoitWolf

from . import core

ASSETS = {
    '600036.SH': '招商银行', '601318.SH': '中国平安', '601166.SH': '兴业银行',
    '600519.SH': '贵州茅台', '000858.SZ': '五粮液', '000333.SZ': '美的集团',
    '000651.SZ': '格力电器', '300750.SZ': '宁德时代', '002594.SZ': '比亚迪',
    '601012.SH': '隆基绿能', '600900.SH': '长江电力', '601088.SH': '中国神华',
    '600028.SH': '中国石化', '600030.SH': '中信证券', '002415.SZ': '海康威视',
    '688981.SH': '中芯国际', '510300.SH': '沪深300ETF',
    '510500.SH': '中证500ETF', '159915.SZ': '创业板ETF',
}
STOCKS = list(ASSETS)[:16]
BENCHMARK = '510300.SH'
ENDPOINT = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
PUBLIC = core.ROOT / 'app/static/research'


def download(symbol, directory, start, end):
    code, exchange = symbol.split('.')
    market = 1 if exchange == 'SH' else 0
    params = dict(secid=f'{market}.{code}', klt='101', fqt='2', beg=start.replace('-', ''),
                  end=end.replace('-', ''), fields1='f1,f2,f3,f4,f5,f6',
                  fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
    response = requests.get(ENDPOINT, params=params, timeout=(5, 30))
    response.raise_for_status()
    payload = response.json()
    data = payload.get('data')
    if payload.get('rc', 0) or not data or data.get('code') != code or data.get('market') != market:
        raise ValueError(f'Unexpected source identity/response: {symbol}')
    path = directory / f'{symbol}.json'
    path.write_bytes(response.content)
    return dict(symbol=symbol, endpoint=ENDPOINT, parameters=params, fetched_at=core.iso(),
                sha256=hashlib.sha256(response.content).hexdigest(), file=path.name,
                adjustment='hfq / source fqt=2', source_name=data.get('name'))


def prices_from_snapshot(directory, manifest, calendar):
    prices, quality = {}, []
    for item in manifest['sources']:
        raw = (directory / item['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + item['symbol'])
        rows = []
        seen = set()
        for line in json.loads(raw)['data']['klines']:
            cells = line.split(',')
            if len(cells) != 11:
                raise ValueError('Kline schema changed')
            day = cells[0]
            if not manifest['start'] <= day <= manifest['end']:
                raise ValueError('Source date outside requested window')
            if day in seen or day not in calendar:
                raise ValueError('Duplicate or non-session date: ' + day)
            seen.add(day)
            op, close, high, low = map(float, cells[1:5])
            if not all(np.isfinite([op, close, high, low])) or min(op, close, high, low) <= 0:
                raise ValueError('Invalid adjusted OHLC')
            if not low <= min(op, close) <= max(op, close) <= high:
                raise ValueError('OHLC bounds violation')
            rows.append((day, close))
        series = pd.Series(dict(rows), dtype=float).sort_index()
        if series.empty:
            raise ValueError('Empty source: ' + item['symbol'])
        prices[item['symbol']] = series
        quality.append(dict(symbol=item['symbol'], rows=len(series), first=series.index[0],
                            last=series.index[-1], missing_sessions=len(set(calendar)-set(series.index))))
    return pd.DataFrame(prices).reindex(calendar), quality


def log_returns(prices):
    """Calendar must be reindexed before this call: never bridge a missing session."""
    if ((prices <= 0) & prices.notna()).any().any():
        raise ValueError('Log returns need strictly positive prices')
    return np.log(prices).diff().replace([np.inf, -np.inf], np.nan)


def residualize(returns, market, fit=None):
    x = np.column_stack([np.ones(len(returns)), market.to_numpy()])
    beta = np.linalg.lstsq(x, returns.to_numpy(), rcond=None)[0] if fit is None else fit
    residuals = pd.DataFrame(returns.to_numpy()-x @ beta, index=returns.index, columns=returns.columns)
    return residuals, beta


def block_ci(x, y, seed=73, repetitions=1000, block=10):
    """Circular paired block bootstrap; exploratory intervals, no multiplicity claim."""
    rng = np.random.default_rng(seed)
    n = len(x)
    starts = rng.integers(0, n, size=(repetitions, (n+block-1)//block))
    idx = ((starts[..., None]+np.arange(block)) % n).reshape(repetitions, -1)[:, :n]
    xx, yy = np.asarray(x)[idx], np.asarray(y)[idx]
    xx -= xx.mean(axis=1, keepdims=True)
    yy -= yy.mean(axis=1, keepdims=True)
    corr = (xx*yy).sum(axis=1)/np.sqrt((xx**2).sum(axis=1)*(yy**2).sum(axis=1))
    return np.quantile(corr[np.isfinite(corr)], [.025, .975]).tolist()


def analyze(prices):
    sparse_returns = log_returns(prices)
    returns = sparse_returns.dropna(how='any')
    if len(returns) < 500 or (returns.std() == 0).any():
        raise ValueError('Insufficient aligned nonconstant return observations')
    stock = returns[STOCKS]
    corr = stock.corr()
    residuals, _ = residualize(stock, returns[BENCHMARK])
    split = int(len(returns)*.8)
    train, holdout = returns.iloc[:split], returns.iloc[split:]
    train_resid, beta = residualize(train[STOCKS], train[BENCHMARK])
    holdout_resid, _ = residualize(holdout[STOCKS], holdout[BENCHMARK], fit=beta)
    train_corr = train[STOCKS].corr()
    sample = stock.cov()
    lw = LedoitWolf().fit(stock)
    lw_frame = pd.DataFrame(lw.covariance_, index=STOCKS, columns=STOCKS)
    distance = np.sqrt(np.clip((1-corr.to_numpy())/2, 0, 1))
    np.fill_diagonal(distance, 0)
    tree = linkage(squareform(distance, checks=False), method='average', optimal_ordering=True)
    order = [STOCKS[i] for i in leaves_list(tree)]
    cluster_ids = fcluster(tree, t=4, criterion='maxclust')
    eig = np.linalg.eigvalsh(corr.to_numpy())[::-1]
    pairs = []
    for a, b in combinations(STOCKS, 2):
        rolling = sparse_returns[a].rolling(60, min_periods=60).corr(sparse_returns[b]).dropna()
        pairs.append(dict(a=a, b=b, names=f'{ASSETS[a]} / {ASSETS[b]}',
            correlation=float(corr.loc[a,b]), spearman=float(stock[[a,b]].corr(method='spearman').iloc[0,1]),
            residual=float(residuals[[a,b]].corr().iloc[0,1]), train=float(train_corr.loc[a,b]),
            holdout=float(holdout[[a,b]].corr().iloc[0,1]),
            holdout_residual=float(holdout_resid[[a,b]].corr().iloc[0,1]),
            recent60=float(stock[[a,b]].tail(60).corr().iloc[0,1]),
            recent252=float(stock[[a,b]].tail(252).corr().iloc[0,1]),
            covariance_daily=float(sample.loc[a,b]), covariance_annual=float(sample.loc[a,b]*252),
            rolling_min=float(rolling.min()), rolling_max=float(rolling.max()),
            rolling_last_date=rolling.index[-1] if len(rolling) else None))
    pairs.sort(key=lambda p:p['train'], reverse=True)
    for pair in pairs[:8]:
        pair['holdout_ci95'] = block_ci(holdout[pair['a']].values, holdout[pair['b']].values)
    matrices = {'correlation':corr, 'residual':residuals.corr(),
                'recent60':stock.tail(60).corr(), 'recent252':stock.tail(252).corr(),
                'sample_cov_daily':sample, 'sample_cov_annual':sample*252,
                'ledoit_wolf_annual':lw_frame*252, 'price_cov_yuan2':prices[STOCKS].dropna().cov(),
                'all_assets_correlation':returns.corr()}
    result = dict(assets=ASSETS, stocks=STOCKS, benchmark=BENCHMARK, order=order,
                  return_start=returns.index[0], return_end=returns.index[-1], observations=len(returns),
                  excluded_return_sessions=len(sparse_returns)-1-len(returns),
                  train_start=train.index[0], train_end=train.index[-1], train_rows=len(train),
                  holdout_start=holdout.index[0], holdout_end=holdout.index[-1], holdout_rows=len(holdout),
                  recent60_start=stock.tail(60).index[0], recent252_start=stock.tail(252).index[0],
                  shrinkage=float(lw.shrinkage_), covariance_min_eigenvalue=float(np.linalg.eigvalsh(lw.covariance_).min()),
                  pca_variance_ratio=(eig/eig.sum()).tolist(), annualization=252,
                  clusters={str(i):[s for s, c in zip(STOCKS, cluster_ids) if c==i] for i in sorted(set(cluster_ids))},
                  pairs=pairs, matrices={k:v.to_dict(orient='index') for k,v in matrices.items()},
                  large_move_counts={s:int((returns[s].abs()>.15).sum()) for s in returns},
                  rolling={p['a']+'_'+p['b']:{k:float(v) for k,v in sparse_returns[p['a']].rolling(60, min_periods=60).corr(sparse_returns[p['b']]).dropna().items()} for p in pairs[:4]})
    return result, matrices, returns


def plots(result, matrices, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, findfont
    custom = core.os.environ.get('FINANCE_FONT_PATH')
    font = FontProperties(fname=custom) if custom else FontProperties(family=['Microsoft YaHei','Noto Sans CJK SC','Noto Sans CJK JP','SimHei','Arial Unicode MS','DejaVu Sans'])
    # Windows normally has Microsoft YaHei; Linux may install Noto CJK.
    if not custom:
        font = FontProperties(fname=findfont(font))
    plt.rcParams.update({'font.family':font.get_name(), 'axes.unicode_minus':False, 'figure.facecolor':'#f7f9fc'})
    order = result['order']
    fig, axs = plt.subplots(1, 2, figsize=(17, 8), constrained_layout=True)
    adjustment = '后复权' if result['provenance']['sources'][0]['parameters']['fqt']=='2' else '前复权'
    for ax, key, title in zip(axs, ['correlation','residual'], [adjustment+'日收益率相关性', '扣除沪深300共同波动后']):
        im = ax.imshow(matrices[key].loc[order,order], cmap='RdBu_r', vmin=-1, vmax=1)
        labels = [ASSETS[s] for s in order]
        ax.set_xticks(range(len(order)), labels, rotation=65, ha='right', fontsize=9)
        ax.set_yticks(range(len(order)), labels, fontsize=9)
        ax.set_title(title, pad=18, fontsize=15)
        for i,a in enumerate(order):
            for j,b in enumerate(order):
                v = matrices[key].loc[a,b]
                ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=6,color='white' if abs(v)>.65 else '#17283a')
    fig.colorbar(im, ax=axs, shrink=.65, label='Pearson r')
    fig.suptitle(f'观澜 · 16只股票 / {result["return_start"]} — {result["return_end"]}', fontsize=17)
    fig.savefig(output/'correlation.png', dpi=170);plt.close(fig)
    fig, ax = plt.subplots(figsize=(13, 5), constrained_layout=True)
    for pair in result['pairs'][:4]:
        series = result['rolling'][pair['a']+'_'+pair['b']]
        ax.plot(pd.to_datetime(list(series)), list(series.values()), label=pair['names'], linewidth=1.4)
    ax.axvline(pd.Timestamp(result['holdout_start']), color='#718096', linestyle='--', label='留出期开始')
    ax.set(title='训练期筛选的前4组 · 60交易日滚动相关性', ylim=(-1,1), ylabel='Pearson r')
    ax.legend(ncol=2, loc='lower left');ax.grid(alpha=.15)
    fig.savefig(output/'rolling.png', dpi=170);plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--reference', type=Path, help='Previously saved qfq analysis for adjustment sensitivity')
    args = parser.parse_args()
    if args.snapshot:
        directory = args.snapshot
        manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    else:
        local = core.now().astimezone(core.SH)
        start = core.history_start()
        # Conservative finalized daily cutoff, shared with collector's 16:00 rule.
        bound = local.date() if local.hour >= 16 else local.date()-timedelta(days=1)
        end = max(d for d in core.calendar_dates() if d<=bound.isoformat())
        directory = core.DATA/'research'/local.strftime('%Y%m%dT%H%M%S')
        directory.mkdir(parents=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            sources = list(pool.map(lambda s:download(s,directory,start,end), ASSETS))
        manifest = dict(start=start, end=end, generated_at=core.iso(), sources=sources,
                        calendar=[d for d in core.calendar_dates() if start<=d<=end],
                        universe_selection='固定选择16只代表性存续股票，另加3只ETF；不是全市场随机样本，存在选择与存续偏差',
                        adjustment='源后复权 fqt=2；价格序列收益代理，非经过独立核验的分红再投资总回报；源算法与历史可修订')
        (directory/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    prices, quality = prices_from_snapshot(directory, manifest, manifest['calendar'])
    result, matrices, returns = analyze(prices)
    result.update(generated_at=manifest['generated_at'], window_start=manifest['start'],
                  source_end=manifest['end'], quality=quality, provenance=manifest)
    if args.reference:
        raw_reference = args.reference.read_bytes()
        reference = json.loads(raw_reference)
        if any(reference[k] != result[k] for k in ['return_start','return_end','observations','stocks']):
            raise ValueError('Sensitivity comparison requires matching dates and universe')
        old = {(p['a'],p['b']):p for p in reference['pairs']}
        changes = []
        for p in result['pairs']:
            p['qfq_correlation'] = old[(p['a'],p['b'])]['correlation']
            p['adjustment_delta'] = p['correlation']-p['qfq_correlation']
            changes.append(dict(names=p['names'], delta=p['adjustment_delta']))
        changes.sort(key=lambda p:abs(p['delta']), reverse=True)
        result['adjustment_sensitivity'] = dict(max_absolute_delta=abs(changes[0]['delta']), largest_changes=changes[:5],
            reference_sha256=hashlib.sha256(raw_reference).hexdigest(), reference_generated_at=reference['generated_at'],
            note='同一窗口的前复权/后复权全样本相关性对照；二者均为同一上游，非独立来源核验')
    PUBLIC.mkdir(parents=True, exist_ok=True)
    prices.to_csv(directory/'adjusted_close.csv', index_label='date')
    returns.to_csv(directory/'log_returns.csv', index_label='date')
    for key, matrix in matrices.items():
        matrix.to_csv(PUBLIC/(key+'.csv'), index_label='symbol')
    pd.DataFrame(result['pairs']).to_csv(PUBLIC/'pairs.csv', index=False)
    plots(result, matrices, PUBLIC)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    (directory/'analysis.json').write_text(encoded, encoding='utf-8')
    temp = PUBLIC/'analysis.json.tmp';temp.write_text(encoded, encoding='utf-8');temp.replace(PUBLIC/'analysis.json')
    (PUBLIC/'provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(snapshot=str(directory), rows=result['observations'], dates=[result['return_start'],result['return_end']],
                         quality=quality, pca1=result['pca_variance_ratio'][0], top_pairs=result['pairs'][:8]), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
