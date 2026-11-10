"""Generate a reproducible research DRAFT; evidence review remains necessary."""
import argparse,json,sys,importlib.util
from pathlib import Path
import pandas as pd
SKILLS_DIR=Path(__file__).resolve().parents[2]
def table(d):return d.to_markdown(index=False)
def run(bundle,assumptions=None):
    b=Path(bundle);meta=json.loads((b/'bundle.json').read_text(encoding='utf-8'));out=b/'research';out.mkdir(exist_ok=False)
    f=pd.read_csv(b/'financial.csv',parse_dates=['period']).sort_values('period').set_index('period')
    f['gross_margin_pct']=(1-f.cogs/f.revenue)*100;f['parent_margin_pct']=f.parent_profit/f.revenue*100
    f['cfo_to_consolidated_profit']=f.cfo/f.net_profit
    # Match report period exactly; missing quarters never shift an annual date.
    for col in ['revenue','parent_profit']:
        prior=f[col].reindex(f.index-pd.DateOffset(years=1)).to_numpy()
        f[col+'_yoy_pct']=(f[col].to_numpy()/prior-1)*100
    annual=f[f.index.month.eq(12) if hasattr(f.index.month,'eq') else f.index.month==12].tail(5)
    f.to_csv(out/'financial_metrics.csv',encoding='utf-8-sig');annual.to_csv(out/'annual.csv',encoding='utf-8-sig')
    val=pd.read_csv(b/'raw/valuation.csv');val['数据日期']=pd.to_datetime(val['数据日期']);val=val[(val['数据日期']<=pd.Timestamp(meta['asof']))&(val['数据日期']>=pd.Timestamp(meta['start']))].sort_values('数据日期')
    v=val.iloc[-1];price=pd.read_csv(b/'price_raw.csv').close.iloc[-1];ttm=None
    common=f[['revenue','parent_profit','cfo']].dropna();period=common.index[-1]
    if period.month==12:ttm=float(common.loc[period,'parent_profit'])
    else:
        prior=period-pd.DateOffset(years=1);yr=pd.Timestamp(year=period.year-1,month=12,day=31)
        if prior in common.index and yr in common.index:ttm=float(common.loc[yr,'parent_profit']+common.loc[period,'parent_profit']-common.loc[prior,'parent_profit'])
    eps=ttm/float(v['总股本']) if ttm is not None and float(v['总股本'])>0 else None
    stats={'symbol':meta['symbol'],'name':meta['name'],'latest_financial_period':str(period.date()),'latest_price_date':meta['latest_price_date'],'close':float(price),'eps_ttm_current_shares':eps,'pe_computed':float(price/eps) if eps and eps>0 else None,'vendor_pe':float(v['PE(TTM)']),'vendor_pb':float(v['市净率']),'valuation_date':str(v['数据日期'].date()),'pe_percentile_pct':float((val['PE(TTM)']<=v['PE(TTM)']).mean()*100),'research_status':'DRAFT_REQUIRES_PRIMARY_SOURCE_AND_BUSINESS_REVIEW'}
    (out/'metrics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    if not (b/'quant/metrics.json').exists():
        spec=importlib.util.spec_from_file_location('stock_quant',SKILLS_DIR/'stock-quant/scripts/compute.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);mod.run(b)
    text=f"# {meta['name']}（{meta['symbol']}）研究底稿\n\n状态：待正式公告/商业与行业资料核验，不是完整研究结论。\n\n事实：截至{meta['latest_price_date']}，收盘{price:.2f}元；最近财报期{period.date()}。来源及抓取时间见../bundle.json，财报按公告日过滤。\n\n## 五年年度财务（原始金额元）\n"+table(annual.reset_index()[['period','revenue','parent_profit','cfo','gross_margin_pct','parent_margin_pct','roe_pct']])+"\n\n## 估值\n```json\n"+json.dumps(stats,ensure_ascii=False,indent=2)+"\n```\n"
    if assumptions:
        pars=json.loads(Path(assumptions).read_text(encoding='utf-8'));(out/'assumptions.json').write_text(json.dumps(pars,ensure_ascii=False,indent=2),encoding='utf-8')
        if eps is None or eps<=0:raise ValueError('Positive-earnings PE scenarios inapplicable; choose appropriate valuation method')
        cases=pars['cases']
        if set(cases)!={'悲观','基准','乐观'}:raise ValueError('Three named cases required')
        rows=[]
        for name,a in cases.items():
            if a['growth']<=-1 or not 0<a['pe_low']<=a['pe_high'] or not 0<=a['payout']<=1:raise ValueError('Invalid scenario assumptions')
            e=eps*(1+a['growth']);lo=e*a['pe_low'];hi=e*a['pe_high'];div=e*a['payout']
            rows.append(dict(scenario=name,eps=e,low=lo,high=hi,price_return_low=lo/price-1,price_return_high=hi/price-1,hypothetical_dividend=div,total_return_low=(lo+div)/price-1,total_return_high=(hi+div)/price-1))
        d=pd.DataFrame(rows);d.to_csv(out/'scenarios.csv',index=False,encoding='utf-8-sig')
        sensitivity=pd.DataFrame([{'eps_change':g,**{str(pe):eps*(1+g)*pe for pe in pars['sensitivity_pe']}} for g in pars['sensitivity_growth']]);sensitivity.to_csv(out/'sensitivity.csv',index=False,encoding='utf-8-sig')
        text+='\n## 三情景（假设）\n期限和依据：'+pars['horizon']+'；'+pars['rationale']+'。不保证收益；税费未计，不叠加历史已付分红。\n'+table(d)+'\n\n## 敏感性\n'+table(sensitivity)
    else:text+='\n## 预测与情景\n未提供经说明的假设，不自动填入茅台估值参数。\n'
    text+='\n\n## 待人工核验和研究\n按stock-research/assets/report-template.md补全商业模式、行业/政策、同行、公告原文、风险与重新评估条件；区分事实、预测、假设、判断。底稿不自动给买卖结论。量化指标见../quant/。缺失与失败接口见../bundle.json。禁止自动交易。\n'
    (out/'研究底稿.md').write_text(text,encoding='utf-8');return stats
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--assumptions');a=p.parse_args();print(json.dumps(run(a.bundle,a.assumptions),ensure_ascii=False))
