"""A-share financial indicators: disclosed cumulative periods, not single quarters.
Provider endpoint follows AKShare stock_financial_analysis_indicator_em.
Cache is shared, daily checked on demand, with last-good data on refresh failure.
"""
import math
import threading
from datetime import datetime, timezone, timedelta
import requests
from fastapi import APIRouter, HTTPException
from . import security_research

FIELDS = {
    'revenue':'TOTALOPERATEREVE','profit':'PARENTNETPROFIT','core_profit':'KCFJCXSYJLR',
    'revenue_yoy':'TOTALOPERATEREVETZ','profit_yoy':'PARENTNETPROFITTZ','core_profit_yoy':'KCFJCXSYJLRTZ',
    'roe':'ROEJQ','gross_margin':'XSMLL','net_margin':'XSJLL','debt_ratio':'ZCFZL',
    'current_ratio':'LD','quick_ratio':'SD','cash_per_share':'MGJYXJJE','eps':'EPSJB',
    'receivable_days':'YSZKZZTS','inventory_days':'CHZZTS',
}
LOCKS=[threading.Lock() for _ in range(32)]
TZ=timezone(timedelta(hours=8))

def number(value):
    if value is None or value=='':return None
    value=float(value)
    if not math.isfinite(value):raise ValueError('财务字段包含非有限数值')
    return value

def normalize(raw,symbol,today):
    cutoff=today.replace(year=today.year-5) if not (today.month==2 and today.day==29) else today.replace(year=today.year-5,day=28)
    periods={}
    for r in raw:
        if r.get('SECUCODE')!=symbol:raise ValueError('财务数据证券代码不匹配')
        day=datetime.strptime(r['REPORT_DATE'][:10],'%Y-%m-%d').date()
        notice=r.get('NOTICE_DATE');notice=notice[:10] if notice else None
        if day>today or (notice and datetime.strptime(notice,'%Y-%m-%d').date()>today):continue
        if day<cutoff:continue
        if r.get('CURRENCY')!='CNY':raise ValueError('财务币种不是人民币')
        row=dict(date=day.isoformat(),notice_date=notice,label=r.get('REPORT_DATE_NAME',day.isoformat()),**{k:number(r.get(v)) for k,v in FIELDS.items()})
        if row['revenue'] is None and row['profit'] is None:raise ValueError('缺少营收及利润核心字段')
        if row['revenue'] is not None and row['revenue']<0:raise ValueError('营业总收入为负，需核验数据源')
        if row['debt_ratio'] is not None and row['debt_ratio']<0:raise ValueError('资产负债率异常')
        if row['date'] in periods and periods[row['date']]!=row:raise ValueError('同一报告期出现冲突数据')
        periods[row['date']]=row
    if not periods:raise ValueError('近五年没有可用财务报告')
    return sorted(periods.values(),key=lambda r:r['date'])

def analysis(rows,org_type):
    latest=rows[-1];by_date={r['date']:r for r in rows};year=int(latest['date'][:4]);suffix=latest['date'][4:]
    annual=by_date.get(f'{year-1}-12-31');previous=by_date.get(f'{year-1}{suffix}')
    ttm={}
    for field in ['revenue','profit','core_profit']:
        if suffix=='-12-31':ttm[field]=latest[field]
        elif annual and previous and all(r[field] is not None for r in [latest,annual,previous]):ttm[field]=latest[field]+annual[field]-previous[field]
        else:ttm[field]=None
    financial=org_type not in ('通用',None,'')
    observations=[]
    def add(title,text,basis):observations.append(dict(title=title,text=text,basis=basis))
    r,p,k=latest['revenue_yoy'],latest['profit_yoy'],latest['core_profit_yoy']
    if r is not None and p is not None:
        text='营收与归母利润同步增长' if r>0 and p>0 else '营收与归母利润同步下降' if r<0 and p<0 else '营收与归母利润方向不一致'
        add('增长',text,f'本期累计营收同比 {r:.2f}%，归母利润同比 {p:.2f}%。同比来自数据源；上年亏损时增幅需要结合金额阅读。')
    else:add('增长','同比样本不足','需要本期与上年同期的可比口径。')
    if p is not None and k is not None:
        add('利润质量','归母增长、扣非下降' if p>0 and k<0 else '对照扣非利润观察主营盈利',f'归母利润同比 {p:.2f}%，扣非归母利润同比 {k:.2f}%。差异不直接等同于可持续性判断。')
    cash=latest['cash_per_share']
    if cash is not None:add('现金流','经营现金净流入' if cash>0 else '经营现金净流出' if cash<0 else '经营现金净额为零',f'年初至报告期末，每股经营现金流 {cash:.3f} 元。不是单季度值，也不是自由现金流。')
    else:add('现金流','缺少现金流数据','当前数据源未提供本期每股经营现金流。')
    debt=latest['debt_ratio'];old=previous.get('debt_ratio') if previous else None
    if financial:add('行业口径','金融企业单独解读','银行、保险、证券的杠杆和现金流结构不同，不使用工业企业偿债阈值评判。')
    elif debt is not None:add('负债',f'资产负债率 {debt:.2f}%',f'负债总额 ÷ 资产总额。'+(f'较上年同报告期变化 {debt-old:+.2f} 个百分点。' if old is not None else '缺少上年同期可比值。'))
    return dict(latest=latest,ttm=ttm,observations=observations,financial_industry=financial)

def fetch(symbol):
    response=requests.get('https://datacenter.eastmoney.com/securities/api/data/get',params={
        'type':'RPT_F10_FINANCE_MAINFINADATA','sty':'APP_F10_MAINFINADATA','filter':f'(SECUCODE="{symbol}")',
        'p':1,'ps':40,'sr':-1,'st':'REPORT_DATE','source':'HSF10','client':'PC'},timeout=(5,20))
    response.raise_for_status();data=response.json()
    rows=(data.get('result') or {}).get('data')
    if not isinstance(rows,list) or not rows:raise ValueError('财务数据源没有返回报告')
    return rows

def report(instrument,store,fetcher=fetch,now=None):
    symbol=instrument['symbol'];now=now or datetime.now(TZ);today=now.astimezone(TZ).date()
    if instrument['kind']=='etf':return dict(status='unsupported',message='ETF 不适用上市公司财务分析，请选择 A 股。',instrument=instrument)
    key=f'system/financials/{symbol}.json.gz'
    with LOCKS[sum(map(ord,symbol))%len(LOCKS)]:
        cached=store.get_json(key,optional=True)
        if cached and cached.get('checked_date')==today.isoformat():return cached
        try:
            raw=fetcher(symbol);rows=normalize(raw,symbol,today)
            result=dict(status='ready',instrument={k:instrument[k] for k in ('symbol','name','kind')},source='东方财富财务指标（AKShare 同源接口）',source_url='https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code='+symbol[-2:]+symbol[:6]+'&color=b#/cwfx',generated_at=now.isoformat(),checked_date=today.isoformat(),org_type=raw[0].get('ORG_TYPE'),periods=rows,**analysis(rows,raw[0].get('ORG_TYPE')))
            store.put_json(key,result);return result
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            if cached:return dict(cached,status='stale',refresh_error=str(exc),message='本次更新未成功，显示上次已核验财务数据。')
            raise HTTPException(502,'财务数据暂不可用：'+str(exc))

def routes(store_factory):
    router=APIRouter()
    @router.get('/api/securities/{query}/financials')
    def financials(query:str):return report(security_research.resolve(query),store_factory())
    return router
