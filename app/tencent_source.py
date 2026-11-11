"""Tencent public-source adapter; exact response bytes retained as evidence."""
import base64
from datetime import date,datetime,timedelta
import hashlib
import json
import re
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor
import requests
from . import core

HISTORY='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'
QUOTE='https://qt.gtimg.cn/'


def get(url,params):
    for attempt in range(3):
        try:
            response=requests.get(url,params=params,timeout=(5,18));response.raise_for_status();return response
        except (requests.ConnectionError,requests.Timeout):
            if attempt==2:raise
            time.sleep(.5*(attempt+1))


def tx_code(symbol):
    code,exchange=symbol.split('.');return exchange.lower()+code


def decode_history(content,symbol,adjustment):
    text=content.decode('utf-8-sig');text=text[text.index('{'):]
    payload=json.loads(text)
    if payload.get('code') not in (0,None):raise ValueError('Tencent source returned error')
    data=payload.get('data',{}).get(tx_code(symbol))
    if not isinstance(data,dict):raise ValueError('Tencent source identity mismatch')
    key={0:'day',1:'qfqday',2:'hfqday'}[adjustment]
    # Non-distributing instruments can legitimately expose only unadjusted day data.
    rows=data.get(key)
    if rows is None:rows=data.get('day')
    if not isinstance(rows,list):raise ValueError('Tencent adjusted history unavailable')
    name=None
    qt=data.get('qt',{}).get(tx_code(symbol))
    if isinstance(qt,list) and len(qt)>2 and qt[2]==symbol.split('.')[0]:name=qt[1]
    return rows,name


def rows_from_evidence(payload,symbol,adjustment,start,end):
    if payload.get('symbol')!=symbol or payload.get('adjustment')!=adjustment:raise ValueError('Tencent evidence identity mismatch')
    rows={};name=None
    for source in payload['responses']:
        content=base64.b64decode(source['content_base64'],validate=True)
        if hashlib.sha256(content).hexdigest()!=source['sha256']:raise ValueError('Tencent response checksum mismatch')
        values,source_name=decode_history(content,symbol,adjustment);name=source_name or name
        for cells in values:
            if len(cells)<6:raise ValueError('Tencent kline schema mismatch')
            day=cells[0]
            if not start<=day<=end:continue
            value=list(cells[:6])
            if day in rows and rows[day]!=value:raise ValueError('Conflicting Tencent history rows')
            rows[day]=value
    return [rows[k] for k in sorted(rows)],name


def fetch_one(spec,directory,start,end):
    symbol,adjustment=spec;adjust={0:'',1:'qfq',2:'hfq'}[adjustment];responses=[]
    first=int(start[:4]);last=int(end[:4])
    for year in range(first,last+1,2):
        params={'param':f'{tx_code(symbol)},day,{year}-01-01,{min(year+1,last)}-12-31,640,{adjust}'}
        response=get(HISTORY,params)
        # Validate before writing the wrapper, keeping original bytes rather than a fabricated EM payload.
        decode_history(response.content,symbol,adjustment)
        responses.append({'endpoint':HISTORY,'parameters':params,'fetched_at':core.iso(),'sha256':hashlib.sha256(response.content).hexdigest(),'content_base64':base64.b64encode(response.content).decode()})
    payload={'provider':'tencent','symbol':symbol,'adjustment':adjustment,'responses':responses}
    rows,name=rows_from_evidence(payload,symbol,adjustment,start,end)
    if not rows:raise ValueError('Empty Tencent history: '+symbol)
    filename=f'{symbol}.{adjustment}.tencent.json';content=core.dumps(payload).encode();(directory/filename).write_bytes(content)
    return {'provider':'tencent','symbol':symbol,'adjustment':adjustment,'file':filename,'sha256':hashlib.sha256(content).hexdigest(),'endpoint':HISTORY,'fetched_at':core.iso(),'source_name':name,'amount_unit':'not_provided','volume_unit':'source_unverified'}


def parse_quotes(content,symbols):
    text=content.decode('gb18030');result={}
    for symbol in symbols:
        match=re.search(r'v_'+re.escape(tx_code(symbol))+r'="([^"]*)"',text)
        if not match:continue
        cells=match.group(1).split('~')
        if len(cells)<33 or cells[2]!=symbol.split('.')[0]:continue
        try:
            price=float(cells[3]);previous=float(cells[4]);stamp=datetime.strptime(cells[30],'%Y%m%d%H%M%S').replace(tzinfo=core.SH)
            if min(price,previous)<=0 or stamp>core.now().astimezone(core.SH)+timedelta(minutes=5):continue
            result[symbol]={'symbol':symbol,'name':cells[1],'price':price,'previous_close':previous,'pct_change':(price/previous-1)*100,'event_time':stamp.isoformat(),'volume':core.numeric(cells[6],nonnegative=True)}
        except (ValueError,IndexError):continue
    return result


def quote_snapshot(symbol,directory):
    fetched=core.iso()
    try:
        params={'q':tx_code(symbol)};response=get(QUOTE,params);values=parse_quotes(response.content,[symbol])
        if symbol not in values:raise ValueError('Tencent quote unavailable')
        filename=symbol+'.tencent.spot.txt';(directory/filename).write_bytes(response.content)
        return dict(values[symbol],provider='tencent',status='ok',fetched_at=fetched,endpoint=QUOTE,parameters=params,file=filename,sha256=hashlib.sha256(response.content).hexdigest())
    except Exception as exc:return {'symbol':symbol,'status':'error','error':type(exc).__name__,'fetched_at':fetched,'provider':'tencent'}


def market_quotes(known):
    symbols=list(known);groups=[symbols[i:i+100] for i in range(0,len(symbols),100)]
    def fetch(group):return parse_quotes(get(QUOTE,{'q':','.join(tx_code(s) for s in group)}).content,group)
    rows={};stamp=core.iso()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(fetch,groups):
            for symbol,value in result.items():
                rows[symbol]={'symbol':symbol,'close':value['price'],'pct_change_source':value['pct_change'],'event_time':value['event_time'],'fetched_at':stamp,'volume_raw':value['volume'],'amount_raw':None,'source':'腾讯证券 · 云端备用快照','quality':'snapshot','time_semantics':'source_update'}
    if len(rows)<len(known)*.8:raise ValueError('Tencent bulk quotes incomplete')
    return {'fetched_at':stamp,'items':rows,'source':'Tencent'}


def refresh_series(symbol,interval,previous,store):
    fetched=core.iso();batch_id=core.uuid.uuid4().hex;responses=[]
    if interval=='1d':
        bound=core.now().astimezone(core.SH)
        end=bound.date() if bound.hour>=16 else bound.date()-timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory:
            spec=fetch_one((symbol,0),core.Path(directory),core.history_start(),end.isoformat())
            evidence=json.loads((core.Path(directory)/spec['file']).read_text())
            values,_=rows_from_evidence(evidence,symbol,0,core.history_start(),end.isoformat())
            responses=evidence['responses']
        raw=[dict(zip(['日期','开盘','收盘','最高','最低','成交量'],cells[:6])) for cells in values]
        endpoint=HISTORY
    elif interval=='5m':
        endpoint='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline';params={'param':tx_code(symbol)+',m5,,320'}
        response=get(endpoint,params);payload=response.json();data=payload.get('data',{}).get(tx_code(symbol),{})
        values=data.get('m5')
        if not isinstance(values,list):raise ValueError('Tencent minute kline unavailable')
        raw=[]
        for cells in values:
            if len(cells)<6:raise ValueError('Tencent minute kline schema mismatch')
            row=dict(zip(['时间','开盘','收盘','最高','最低','成交量'],cells[:6]));row['时间']=datetime.strptime(row['时间'],'%Y%m%d%H%M').isoformat();raw.append(row)
        responses=[{'endpoint':endpoint,'parameters':params,'fetched_at':fetched,'sha256':hashlib.sha256(response.content).hexdigest(),'content_base64':base64.b64encode(response.content).decode()}]
    elif interval=='1m':
        endpoint='https://web.ifzq.gtimg.cn/appstock/app/minute/query';params={'code':tx_code(symbol)}
        response=get(endpoint,params);data=response.json().get('data',{}).get(tx_code(symbol),{}).get('data',{})
        day=datetime.strptime(data.get('date',''),'%Y%m%d').date().isoformat();raw=[]
        for line in data.get('data',[]):
            cells=line.split()
            if len(cells)<3:raise ValueError('Tencent trend schema mismatch')
            raw.append({'时间':day+'T'+cells[0][:2]+':'+cells[0][2:4]+':00','收盘':cells[1],'成交量':cells[2],'成交额':cells[3] if len(cells)>3 else None})
        responses=[{'endpoint':endpoint,'parameters':params,'fetched_at':fetched,'sha256':hashlib.sha256(response.content).hexdigest(),'content_base64':base64.b64encode(response.content).decode()}]
    else:raise ValueError('Unsupported interval')
    accepted=[];issues={}
    for row in raw:
        record,flags=core.normalize(row,symbol,interval,fetched)
        for flag in flags:issues[flag]=issues.get(flag,0)+1
        if record:
            record.update(source='腾讯证券 · 云端直连',batch_id=batch_id,fetched_at=fetched)
            if interval=='1m':
                record['volume_unit']='source_unverified_cumulative';record['flags']=list(set(record['flags']+['cumulative_volume_and_amount']))
            accepted.append(record)
    if not accepted:raise ValueError('No complete valid Tencent records')
    # Preserve the previous archive, then replace only observations actually supplied by this source.
    merged={r['event_time']:r for r in previous['records'] if r['event_time']>=core.history_start()}
    for row in accepted:merged[row['event_time']]=row
    evidence={'provider':'tencent','symbol':symbol,'interval':interval,'fetched_at':fetched,'responses':responses,'rows':raw}
    raw_key='evidence/'+symbol+'/'+batch_id+'.json.gz';store.put_json(raw_key,evidence)
    batch={'id':batch_id,'symbol':symbol,'interval':interval,'endpoint':endpoint,'parameters':'{}','started_at':fetched,'finished_at':core.iso(),'status':'ok','rows_in':len(raw),'rows_accepted':len(accepted),'rows_rejected':len(raw)-len(accepted),'changed':0,'issues':core.dumps(issues),'raw_path':raw_key,'raw_sha256':hashlib.sha256(core.dumps(evidence).encode()).hexdigest(),'cloud_raw':True}
    result={'records':[merged[k] for k in sorted(merged)],'last_batch':batch}
    store.put_json('live/'+symbol+'/'+interval+'.json.gz',result);store.put_json('batches/'+batch_id+'.json.gz',batch)
    return result
