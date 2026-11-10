"""Portable collection for independently installed Codex stock skills.
"""
import argparse, json, os, re, sys, threading
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import requests

def symbol_parts(symbol):
    match=re.fullmatch(r'(\d{6})\.(SH|SZ)',symbol.upper())
    if not match:raise ValueError('Use six digits plus .SH or .SZ')
    code,exchange=match.groups()
    if (exchange=='SH' and not code.startswith('6')) or (exchange=='SZ' and not code.startswith(('0','3'))):
        raise ValueError('A-share code/exchange mismatch or unsupported market')
    return code,exchange
def clean_dates(df,datecol,end,announcement=None):
    d=df.copy();d[datecol]=pd.to_datetime(d[datecol],errors='coerce')
    d=d[d[datecol].notna()&(d[datecol]<=pd.Timestamp(end))]
    if announcement:
        d[announcement]=pd.to_datetime(d[announcement],errors='coerce')
        d=d[d[announcement].notna()&(d[announcement]<=pd.Timestamp(end))]
    return d.sort_values(datecol).drop_duplicates(datecol,keep='last')
def check_prices(df):
    if df.empty or not df.date.is_unique:raise ValueError('Empty/duplicate price dates')
    if not (df.high>=df[['open','close','low']].max(axis=1)).all():raise ValueError('Invalid OHLC high')
    if not (df.low<=df[['open','close','high']].min(axis=1)).all():raise ValueError('Invalid OHLC low')
    if (df.close<=0).any():raise ValueError('Nonpositive price')

def fetch_announcements(code,exchange,start,end):
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}
    r=requests.post('https://www.cninfo.com.cn/new/information/topSearch/query',data={'keyWord':code,'maxNum':10},headers=headers,timeout=20)
    r.raise_for_status();found=[x for x in r.json() if str(x.get('code'))==code and x.get('category')=='A股']
    if len(found)!=1:raise ValueError('Ambiguous CNINFO identity mapping')
    org=found[0]['orgId']
    r=requests.post('https://www.cninfo.com.cn/new/hisAnnouncement/query',data={'pageNum':1,'pageSize':30,'column':'sse' if exchange=='SH' else 'szse','tabName':'fulltext','stock':code+','+org,'seDate':str(pd.Timestamp(start).date())+'~'+str(pd.Timestamp(end).date())},headers=headers,timeout=20)
    r.raise_for_status();df=pd.DataFrame(r.json().get('announcements') or [])
    if df.empty:raise ValueError('No announcements')
    if not df.secCode.astype(str).str.zfill(6).eq(code).all():raise ValueError('Announcement security mismatch')
    df['published']=pd.to_datetime(df.announcementTime,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
    df=df[df.published<pd.Timestamp(end)+pd.Timedelta(days=1)].drop_duplicates('announcementId')
    df['source_url']='https://static.cninfo.com.cn/'+df.adjunctUrl
    return df

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--symbol',required=True);ap.add_argument('--asof');ap.add_argument('--years',type=int,default=5);ap.add_argument('--out',required=True)
    args=ap.parse_args();code,exchange=symbol_parts(args.symbol);symbol=code+'.'+exchange
    now=datetime.now(ZoneInfo('Asia/Shanghai'));end=pd.Timestamp(args.asof or str(now.date() if now.hour>=16 else now.date()-timedelta(days=1)))
    if end>pd.Timestamp(now.date()):raise ValueError('Future asof rejected')
    if not 1<=args.years<=20:raise ValueError('years must be 1..20')
    start=end-pd.DateOffset(years=args.years);out=Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('Output directory must be new/empty; no overwrite')
    (out/'raw').mkdir(parents=True,exist_ok=True)
    from portable_runtime import get_obb
    obb=get_obb();import akshare as ak
    credentials=obb.user.credentials;secret_values=[]
    for name in ['tushare_api_key','akshare_api_key']:
        value=getattr(credentials,name,None)
        if value:secret_values.append(value.get_secret_value())
    def redact(s):
        for value in secret_values:s=s.replace(value,'[REDACTED]')
        return s[:1000]
    original=requests.Session.request
    def bounded(self,method,url,**kw):
        if not kw.get('timeout'):kw['timeout']=(8,20)
        return original(self,method,url,**kw)
    requests.Session.request=bounded
    entries=[];frames={};guard=threading.Lock()
    def job(name,fn,provider,params,unit,adjustment='',identity_col=None):
        rec=dict(name=name,provider=provider,params=params,unit=unit,adjustment=adjustment,fetched_at=datetime.now().astimezone().isoformat())
        try:
            df=fn()
            if not isinstance(df,pd.DataFrame) or df.empty:raise ValueError('Empty response')
            if identity_col:
                got=df[identity_col].astype(str).str.replace(r'\.0$','',regex=True).str.zfill(6)
                if not got.eq(code).all():raise ValueError('Returned security identity mismatch')
            df.to_csv(out/'raw'/(name+'.csv'),index=True,encoding='utf-8-sig')
            with guard:frames[name]=df
            rec.update(status='success',rows=len(df),file='raw/'+name+'.csv')
        except Exception as e:rec.update(status='failed',error=redact(str(e)),error_type=type(e).__name__)
        with guard:
            entries.append(rec)
            with (out/'queries.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(rec,ensure_ascii=False,default=str)+'\n')
        return rec
    jobs=[]
    def add(name,fn,source,p,unit,adj='',ident=None):jobs.append((name,fn,source,p,unit,adj,ident))
    for adj in ['', 'qfq','hfq']:
        p=dict(symbol=code,start_date=start.strftime('%Y%m%d'),end_date=end.strftime('%Y%m%d'),period='daily',adjust=adj)
        add('price_'+(adj or 'raw'),lambda p=p:ak.stock_zh_a_hist(**p),'AKShare/Eastmoney',p,'CNY/share;volume=lots(100 shares);amount=CNY;turnover=%',adj or 'unadjusted','股票代码')
    p=dict(symbol=code,provider='akshare',start_date=str(start.date()),end_date=str(end.date()),use_cache=False)
    add('sdk_price',lambda p=p:obb.equity.price.historical(**p).to_dataframe(),'OpenBB V4/AKShare',p,'CNY/share;volume=lots','unadjusted')
    p=dict(symbol=exchange.lower()+code,start_date=start.strftime('%Y%m%d'),end_date=end.strftime('%Y%m%d'),adjust='',timeout=20)
    add('cross_price',lambda p=p:ak.stock_zh_a_hist_tx(**p),'AKShare/Tencent',p,'CNY/share;only close used','unadjusted')
    for name,fun in [('income','stock_profit_sheet_by_report_em'),('balance','stock_balance_sheet_by_report_em'),('cashflow','stock_cash_flow_sheet_by_report_em')]:
        p={'symbol':exchange+code};f=getattr(ak,fun)
        add(name,lambda p=p,f=f:f(**p),'AKShare/Eastmoney/'+fun,p,'CNY;REPORT_DATE and NOTICE_DATE','', 'SECURITY_CODE')
    for name,fun,p,unit in [
        ('indicators','stock_financial_analysis_indicator_em',{'symbol':symbol,'indicator':'按报告期'},'CNY;ratios=%'),
        ('valuation','stock_value_em',{'symbol':code},'CNY/share;marketcap=CNY;PE/PB=times'),
        ('dividend','stock_fhps_detail_em',{'symbol':code},'CNY per 10 shares'),
        ('dividend_cross','stock_history_dividend_detail',{'symbol':code,'indicator':'分红'},'CNY per 10 shares'),
        ('segments','stock_zygc_em',{'symbol':exchange+code},'CNY;ratios=0..1'),
        ('flow','stock_individual_fund_flow',{'stock':code,'market':exchange.lower()},'CNY;shares classified by order size'),
        ('macro','bond_zh_us_rate',{'start_date':start.strftime('%Y%m%d')},'yield=%')]:
        f=getattr(ak,fun);add(name,lambda f=f,p=p:f(**p),'AKShare/'+fun,p,unit,ident='SECURITY_CODE' if name=='indicators' else None)
    add('news',lambda:obb.news.company(code,provider='akshare').to_dataframe(),'OpenBB V4/AKShare',{'symbol':code},'titles, timestamps, URLs; not verified facts')
    def announcements():
        return fetch_announcements(code,exchange,start,end)
    add('announcements',announcements,'CNINFO',{'symbol':symbol,'pages':1},'publisher timestamps; limited recent list')
    token=getattr(credentials,'tushare_api_key',None)
    if token:
        import tushare as ts
        pro=ts.pro_api(token.get_secret_value(),timeout=20)
        for ep in ['daily','daily_basic','adj_factor','income','balancesheet','cashflow']:
            p={'ts_code':symbol,'start_date':start.strftime('%Y%m%d'),'end_date':end.strftime('%Y%m%d')}
            add('tushare_'+ep,lambda ep=ep,p=p:pro.query(ep,**p),'Tushare Pro/'+ep,p,'native Tushare units; not merged without field verification')
    else:entries.append({'name':'tushare','status':'unavailable','reason':'No token configured; public sources used'})
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda t:job(*t),jobs))
    identity=frames.get('income',pd.DataFrame())
    if identity.empty:raise ValueError('Cannot establish financial identity; collection incomplete, see queries.jsonl')
    name=str(identity.SECURITY_NAME_ABBR.iloc[0])
    for key in ['price_raw','price_qfq']:
        d=frames.get(key)
        if d is None:raise ValueError('Required price unavailable: '+key)
        d=d.rename(columns={'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume_lots','成交额':'amount_cny','换手率':'turnover_pct'})
        d=clean_dates(d,'date',end);d=d[d.date>=start];check_prices(d);d['volume_shares']=d.volume_lots*100
        d.to_csv(out/(key+'.csv'),index=False,encoding='utf-8-sig')
    fin=pd.DataFrame()
    mappings={'income':{'OPERATE_INCOME':'revenue','PARENT_NETPROFIT':'parent_profit','NETPROFIT':'net_profit','OPERATE_COST':'cogs','BASIC_EPS':'eps'},'balance':{'TOTAL_ASSETS':'assets','TOTAL_LIABILITIES':'liabilities','TOTAL_PARENT_EQUITY':'parent_equity'},'cashflow':{'NETCASH_OPERATE':'cfo','CONSTRUCT_LONG_ASSET':'capex'},'indicators':{'ROEJQ':'roe_pct'}}
    for key,mp in mappings.items():
        if key not in frames:continue
        d=clean_dates(frames[key],'REPORT_DATE',end,'NOTICE_DATE').set_index('REPORT_DATE')
        z=pd.DataFrame(index=d.index)
        for old,new in mp.items():z[new]=pd.to_numeric(d[old],errors='coerce')
        z[key+'_ann_date']=d.NOTICE_DATE
        fin=z if fin.empty else fin.join(z,how='outer')
    fin.index.name='period';fin.reset_index().to_csv(out/'financial.csv',index=False,encoding='utf-8-sig')
    checks=[];raw=pd.read_csv(out/'price_raw.csv').set_index('date')
    for key in ['cross_price','sdk_price']:
        if key in frames:
            d=frames[key].copy()
            if 'date' not in d:d=d.reset_index()
            d['date']=pd.to_datetime(d.date).dt.strftime('%Y-%m-%d');d=d.set_index('date')
            j=raw[['close']].join(d[['close']].rename(columns={'close':'other'}),how='inner')
            checks.append({'source':key,'overlap':len(j),'max_close_diff':float((j.close-j.other).abs().max()) if len(j) else None,'passed':bool(len(j)>0 and (j.close-j.other).abs().max()<=.02)})
    (out/'crosschecks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    bundle=dict(symbol=symbol,name=name,asof=str(end.date()),start=str(start.date()),latest_price_date=raw.index[-1],price_rows=len(raw),token_present=bool(token),queries=entries,crosschecks=checks,identity_verified=True,units='financial=CNY; price=CNY/share; volume_shares=shares')
    (out/'bundle.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'symbol':symbol,'name':name,'rows':len(raw),'latest':raw.index[-1],'queries_success':sum(r.get('status')=='success' for r in entries),'queries_total':len(entries),'token_present':bool(token)},ensure_ascii=False))
if __name__=='__main__':main()
