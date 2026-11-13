"""Explicit background/offline full audit. Pages only read its compact output.

Local: FINANCE_DATA_DIR=... python scripts/health_audit.py
Cloud: ... --cloud --env-id ENV --key-file /private/runtime-key.json
"""
import argparse,json,sys,time,gzip,tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import core,health_service as hs,data_health as dh,daily_sync

def status_bootstrap(store):
    from app.collector import fetch
    from app.health_status_source import refresh
    evidence=store.get_json(hs.EVIDENCE,optional=True) or {};audit=[]
    for name,suffix,codekey,datekey in [('stock_info_sh_delist','.SH','公司代码','暂停上市日期'),('stock_info_sz_delist','.SZ','证券代码','终止上市日期')]:
        tick=time.monotonic()
        try:
            rows=fetch(name,{})
            for row in rows:
                day=(row.get(datekey) or '')[:10];listed=(row.get('上市日期') or '')[:10]
                if not day:continue
                symbol=str(row[codekey]).zfill(6)+suffix
                evidence.setdefault(symbol,{}).update(source='AKShare '+name+' / exchange termination list',verified_at=core.iso(),delisted_at=day,listed_at=listed,raw=row)
            store.put_json(hs.PREFIX+'status-source/'+name+'.json.gz',dict(fetched_at=core.iso(),rows=rows,interface=name))
            audit.append(dict(interface=name,status='ok',rows=len(rows),seconds=round(time.monotonic()-tick,3)))
        except Exception as exc:audit.append(dict(interface=name,status='failed',**dh.error_info(exc)))
    for name,params,suffix,codekey,datekey in [('stock_info_sh_name_code',{'symbol':'主板A股'},'.SH','证券代码','上市日期'),('stock_info_sh_name_code',{'symbol':'科创板'},'.SH','证券代码','上市日期'),('stock_info_sz_name_code',{},'.SZ','A股代码','A股上市日期'),('stock_info_bj_name_code',{},'.BJ','证券代码','上市日期')]:
        try:
            rows=fetch(name,params)
            for row in rows:
                listed=(row.get(datekey) or '')[:10];code=str(row.get(codekey) or '')
                if not listed or not code:continue
                evidence.setdefault(code.zfill(6)+suffix,{}).update(source='AKShare '+name+' / exchange listing dates',verified_at=core.iso(),listed_at=listed)
            audit.append(dict(interface=name,parameters=params,status='ok',rows=len(rows)))
        except Exception as exc:audit.append(dict(interface=name,status='failed',**dh.error_info(exc)))
    store.put_json(hs.EVIDENCE,evidence)
    target=daily_sync.freshness(None,core.calendar_dates())['expected_date']
    if target:refresh(store,target)
    store.put_json(hs.PREFIX+'status-source/bootstrap.json.gz',dict(at=core.iso(),interfaces=audit))
    return audit

def cloud_audit(store):
    tick=time.monotonic();current=store.get_json('system/current.json.gz');catalog=store.get_json(current['catalog']);calendar=core.calendar_dates();inventory=core.universe()
    import hashlib
    checkpoint=Path(tempfile.gettempdir())/('oldpig-health-audit-'+hashlib.sha256((store.base+catalog['generated_at']+core.now().date().isoformat()).encode()).hexdigest()[:16]+'.json')
    saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else dict(index={},done=[])
    index=saved['index'];done=set(saved['done']);failures=[]
    def save():
        temporary=checkpoint.with_suffix('.tmp');temporary.write_text(json.dumps(dict(index=index,done=sorted(done))));temporary.replace(checkpoint)
    def read(key):
        for attempt in range(3):
            try:
                payload=store.get_json(key)
                if not isinstance(payload,dict):raise ValueError('Missing cloud history object')
                return key,payload,None
            except Exception as exc:
                if attempt==2:return key,None,dh.error_info(exc,(store.key,))
                time.sleep(1+attempt)
    entries=catalog['entries'];chunks=sorted({entry['chunk'] for key,entry in entries.items() if key.endswith('/1d')});lower=core.history_start()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count,(key,chunk,error) in enumerate(pool.map(read,[k for k in chunks if k not in done]),1):
            if error:failures.append(dict(key=key,**error));continue
            for asset,item in chunk.items():
                if not asset.endswith('/1d'):continue
                symbol=asset.split('/')[0];present={r['event_time'][:10] for r in item['records'] if r['event_time'][:10]>=lower}
                missing=dh.gaps(present,calendar)['missing']
                index[symbol]=dict(name=inventory.get(symbol,{}).get('name',symbol),first_date=min(present,default=None),last_date=max(present,default=None),audited_through=max(present,default=None),missing_dates=missing,history_rows=len(present),audit_at=core.iso(),audit_source='cloud immutable archive '+catalog['generated_at'])
            done.add(key)
            if count%25==0:save();print('Archive blocks audited:',len(done),flush=True)
    save()
    overlay_keys=store.list_keys('scheduled/')+[k for k in store.list_keys('live/') if k.endswith('/1d.json.gz')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count,(key,item,error) in enumerate(pool.map(read,[k for k in overlay_keys if k not in done]),1):
            if error:failures.append(dict(key=key,**error));continue
            if '/1d.' not in key:continue
            symbol=key.split('/')[1];present={r['event_time'][:10] for r in item.get('records',[])}
            hs.record(index,symbol,{},present,calendar);done.add(key)
            if count%200==0:save();print('Overlay audit progress:',count,'/',len(overlay_keys),flush=True)
    save()
    store.put_json(hs.PREFIX+'audit-status.json.gz',dict(generated_at=core.iso(),failures=failures,complete=not failures))
    if failures:raise RuntimeError(f'{len(failures)} objects could not be audited; checkpoint saved; original snapshot preserved')
    store.put_json(hs.INDEX,index)
    print(json.dumps(dict(audited_symbols=len(index),chunks=len(chunks),overlays=len(overlay_keys),seconds=round(time.monotonic()-tick,3)),ensure_ascii=False),flush=True)
    return index

def storage_audit(store):
    categories=defaultdict(lambda:dict(bytes=0,objects=0));cursor=None;objects=[]
    while True:
        body=dict(prefix='',limit=1000,with_delimiter=False,sortBy=dict(column='name',order='asc'))
        if cursor:body['cursor']=cursor
        page=store._request('POST','/v1/storages/object/list/'+store.bucket,json=body).json()
        for item in page.get('objects',[]):
            key=item.get('key') or item['name'];size=int(item.get('metadata',{}).get('size',0));group=key.split('/')[0]
            categories[group]['bytes']+=size;categories[group]['objects']+=1;objects.append(dict(key=key,bytes=size))
        if not page.get('hasNext'):break
        next_cursor=page.get('nextCursor')
        if not next_cursor or next_cursor==cursor:raise RuntimeError('Storage metadata cursor missing')
        cursor=next_cursor
    previous=store.get_json(hs.PREFIX+'storage.json.gz',optional=True) or {};stamp=core.iso();total=sum(c['bytes'] for c in categories.values());history=previous.get('history',[])
    history=(history+[dict(at=stamp,bytes=total)])[-30:]
    daily_growth=None
    if len(history)>1:
        seconds=dh.elapsed(history[0]['at'],core.now())
        if seconds and seconds>=86400:daily_growth=(total-history[0]['bytes'])/(seconds/86400)
    result=dict(available=True,generated_at=stamp,total_bytes=total,categories=dict(categories),history=history,daily_growth_bytes=daily_growth,capacity_bytes=None,remaining_days=None,reason='云存储无已核实容量上限；至少两个相隔一天的清点点才估算增速',objects=objects)
    store.put_json(hs.PREFIX+'storage.json.gz',result)
    print(json.dumps(dict(storage_bytes=total,objects=len(objects))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cloud',action='store_true');parser.add_argument('--env-id');parser.add_argument('--key-file',type=Path);parser.add_argument('--skip-history',action='store_true');parser.add_argument('--skip-status',action='store_true');args=parser.parse_args();core.init()
    if args.cloud:
        from app.cloud_store import CloudStore
        store=CloudStore(env=args.env_id,key=json.loads(args.key_file.read_text())['data']['ApiKey'])
        if not args.skip_history:cloud_audit(store)
        storage_audit(store)
    else:
        store=hs.LocalStore()
        if not args.skip_history:print(hs.bootstrap_local(store),flush=True)
    if not args.skip_status:print(status_bootstrap(store),flush=True)
    state=store.get_json(daily_sync.STATE,optional=True) or {}
    snapshot=hs.publish(store,state,store.get_json(hs.INDEX) or {})
    print(json.dumps(dict(state=snapshot['state'],counts=snapshot['counts']),ensure_ascii=False),flush=True)
