"""Fixed two-basket covariance study; frozen provider evidence and no gap filling."""
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from app import core,tencent_source,daily_sync
from app.comovement import block_ci,residualize
OUT=core.ROOT/'research/coal-components';OUT.mkdir(exist_ok=True)
COAL={'601088.SH':'中国神华','601225.SH':'陕西煤业','601898.SH':'中煤能源','600188.SH':'兖矿能源','600985.SH':'淮北矿业','601699.SH':'潞安环能'}
COMP={'002463.SZ':'沪电股份','002384.SZ':'东山精密','002938.SZ':'鹏鼎控股','002916.SZ':'深南电路','600183.SH':'生益科技','300408.SZ':'三环集团'}
ASSETS={**COAL,**COMP,'510300.SH':'沪深300ETF'}
start=core.history_start();end=daily_sync.freshness(None,core.calendar_dates())['expected_date']
if not end:raise ValueError('Calendar does not cover today')
evidence=OUT/'evidence';evidence.mkdir(exist_ok=True)
manifest_file=evidence/'manifest.json'
if '--refresh' in sys.argv or not manifest_file.exists():
 with ThreadPoolExecutor(max_workers=2) as pool:sources=list(pool.map(lambda s:tencent_source.fetch_one((s,2),evidence,start,end),ASSETS))
 manifest={'start':start,'end':end,'assets':ASSETS,'sources':sources,'calendar':[d for d in core.calendar_dates() if start<=d<=end],'fetched_at':core.iso()}
 manifest_file.write_text(core.dumps(manifest))
else:manifest=json.loads(manifest_file.read_text())
frames={};quality=[]
for source in manifest['sources']:
 raw=(evidence/source['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==source['sha256']
 rows,_=tencent_source.rows_from_evidence(json.loads(raw),source['symbol'],2,manifest['start'],manifest['end'])
 values={}
 for cells in rows:
  day=cells[0];o,c,h,l,v=map(float,cells[1:6])
  if day not in manifest['calendar'] or not all(np.isfinite([o,c,h,l,v])) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('Invalid source bar')
  values[day]=c if v>0 else np.nan
 frames[source['symbol']]=pd.Series(values,dtype=float)
 quality.append({'symbol':source['symbol'],'name':ASSETS[source['symbol']],'rows':len(values),'last':max(values),'zero_volume':sum(pd.isna(x) for x in values.values())})
prices=pd.DataFrame(frames).reindex(manifest['calendar']);returns=prices.pct_change(fill_method=None).replace([np.inf,-np.inf],np.nan)
# Complete-panel dates for all reported windows, with explicit exclusions.
aligned=returns.dropna(how='any');assert len(aligned)>500
basket=pd.DataFrame({'煤炭':returns[list(COAL)].mean(axis=1,skipna=False),'元件':returns[list(COMP)].mean(axis=1,skipna=False),'沪深300ETF':returns['510300.SH']})
full=basket.loc[aligned.index];reports=[]
for label,n in [('近五年',len(full)),('近一年',252),('近半年',126),('近60日',60)]:
 f=full.tail(n);r,_=residualize(f[['煤炭','元件']],f['沪深300ETF'])
 reports.append({'window':label,'n':len(f),'start':f.index[0],'end':f.index[-1],'daily_covariance':f['煤炭'].cov(f['元件']),'annualized_covariance':f['煤炭'].cov(f['元件'])*252,'correlation':f['煤炭'].corr(f['元件']),'residual_correlation':r['煤炭'].corr(r['元件']),'coal_volatility':f['煤炭'].std()*np.sqrt(252),'component_volatility':f['元件'].std()*np.sqrt(252),'correlation_ci95':block_ci(f['煤炭'],f['元件'],seed=1337,repetitions=2000,block=min(20,len(f)//5)),'same_direction_fraction':float(((f['煤炭']*f['元件'])>0).mean())})
assets=list(COAL)+list(COMP);corr=aligned[assets].corr();cov=aligned[assets].cov();pairs=[]
for a in COAL:
 for b in COMP:pairs.append({'coal':ASSETS[a],'component':ASSETS[b],'correlation':corr.loc[a,b],'daily_covariance':cov.loc[a,b]})
pairs.sort(key=lambda x:x['correlation'],reverse=True)
rolling=basket['煤炭'].rolling(60,min_periods=60).corr(basket['元件'])
missing=[{'date':d,'missing_symbols':[s for s in ASSETS if pd.isna(returns.loc[d,s])]} for d in returns.index[1:] if returns.loc[d].isna().any()]
result={'generated_at':core.iso(),'data_start':aligned.index[0],'data_end':aligned.index[-1],'observations':len(aligned),'assets':{'煤炭':COAL,'元件':COMP},'source':'Tencent public adjusted daily bars','adjustment':'后复权价格收益代理；不是独立核验的含税后分红总回报','method':'简单日收益率；各篮子六股固定等权；所有股票和510300共同有效交易日；未填充停牌和缺失；无交易成本；非可交易组合回测','selection_bias':'当前人工选定存续股票，不代表完整行业，存在选择/存续偏差。元件样本偏PCB；煤炭样本偏大型煤企。','windows':reports,'cross_pairs':pairs,'within_coal_correlation':float(corr.loc[list(COAL),list(COAL)].to_numpy()[np.triu_indices(6,1)].mean()),'within_component_correlation':float(corr.loc[list(COMP),list(COMP)].to_numpy()[np.triu_indices(6,1)].mean()),'quality':quality,'excluded_sessions':missing,'rolling_latest':None if rolling.dropna().empty else float(rolling.dropna().iloc[-1]),'manifest_hash':hashlib.sha256(manifest_file.read_bytes()).hexdigest(),'code_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(OUT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
cov.rename(index=ASSETS,columns=ASSETS).to_csv(OUT/'covariance-daily.csv',encoding='utf-8-sig');corr.rename(index=ASSETS,columns=ASSETS).to_csv(OUT/'correlation.csv',encoding='utf-8-sig');basket.to_csv(OUT/'basket-returns.csv');rolling.to_csv(OUT/'rolling60.csv')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family':'Noto Sans CJK JP','axes.unicode_minus':False,'figure.facecolor':'#101923','axes.facecolor':'#17222e','text.color':'#e8edf3','axes.labelcolor':'#e8edf3','xtick.color':'#becbd8','ytick.color':'#becbd8','axes.edgecolor':'#344454'})
fig,axs=plt.subplots(1,2,figsize=(16,7),gridspec_kw={'width_ratios':[1.15,1]});im=axs[0].imshow(corr.to_numpy(),vmin=-1,vmax=1,cmap='RdBu_r');labels=[ASSETS[s] for s in assets];axs[0].set_xticks(range(12),labels,rotation=65,ha='right');axs[0].set_yticks(range(12),labels);axs[0].axhline(5.5,color='white');axs[0].axvline(5.5,color='white');axs[0].set_title('代表股日收益率相关性 · 近五年',pad=18)
for i in range(12):
 for j in range(12):axs[0].text(j,i,f'{corr.iloc[i,j]:.2f}',ha='center',va='center',fontsize=7,color='white' if abs(corr.iloc[i,j])>.55 else '#101923')
axs[1].plot(pd.to_datetime(rolling.index),rolling,color='#81a9e8',lw=1.5);axs[1].axhline(0,color='#d4ad76',lw=1);axs[1].set_ylim(-1,1);axs[1].set_title('煤炭 × 元件 · 60交易日滚动相关性',pad=18);axs[1].grid(alpha=.12);axs[1].set_ylabel('相关系数');fig.suptitle('煤炭与电子元件：共同波动研究',fontsize=20);fig.text(.5,.02,f"{result['data_start']} 至 {result['data_end']} · 各6只等权样本 · 复权价格收益代理 · 非行业指数/非投资建议",ha='center',fontsize=10);fig.tight_layout(rect=[0,.05,1,.94]);fig.savefig(OUT/'coal-components.png',dpi=150);plt.close(fig)
lines=['# 煤炭与元件协方差研究','',f"样本：{result['data_start']} 至 {result['data_end']}，共同有效收益日 {len(aligned)} 个。",'',result['method'],result['adjustment'],result['selection_bias'],'','| 区间 | 日协方差（小数收益平方） | 相关性 | 剔除沪深300后相关性 |','|---|---:|---:|---:|']
for r in reports:lines.append(f"| {r['window']} | {r['daily_covariance']:.8f} | {r['correlation']:.3f} | {r['residual_correlation']:.3f} |")
lines+=['','协方差的符号表示同向/反向共同波动，数值受波动率与收益单位影响；相关性用于比较强弱。剔除大盘采用同期OLS残差，仅作描述，不证明行业因果关系。','',f"组内平均相关性：煤炭 {result['within_coal_correlation']:.3f}；元件 {result['within_component_correlation']:.3f}。",'',f"排除 {len(missing)} 个非首日缺失收益交易日，详见 analysis.json。滚动曲线要求连续60个有效交易日，缺失处不跨越拼接。",'','95%区间为固定种子配对移动区块bootstrap（循环区块，2000次）；短窗口不确定性较大，未进行多重比较校正。当前选股回看不能用于证明可交易收益。','', '运行：FINANCE_DATA_DIR=<已有数据目录> python3 scripts/analyze_coal_components.py；默认复用冻结证据，--refresh重新采集。']
(OUT/'README.md').write_text('\n'.join(lines)+'\n');print(json.dumps({k:result[k] for k in ['data_start','data_end','observations','windows','within_coal_correlation','within_component_correlation']},ensure_ascii=False,indent=2))
