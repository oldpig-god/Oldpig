"""Precomputed health index and append-only repair requests; worker is the sole index writer."""
import copy, gzip, hashlib, json, threading, time
from datetime import datetime,timedelta
from pathlib import Path
from . import core, data_health as checks

PREFIX='system/health/'
INDEX=PREFIX+'index.json.gz'
SNAPSHOT=PREFIX+'snapshot.json.gz'
OVERVIEW=PREFIX+'overview.json.gz'
CONFIG=PREFIX+'config.json.gz'
EVIDENCE=PREFIX+'security-status.json.gz'
QUEUE=PREFIX+'requests/'
RESULTS=PREFIX+'results/'
CACHE={};LOCK=threading.Lock()

class LocalStore:
    def path(self,key):return core.DATA/'health-store'/key
    def get_json(self,key,optional=False):
        p=self.path(key)
        if not p.exists():return None
        return json.loads(gzip.decompress(p.read_bytes()))
    def put_json(self,key,value):
        p=self.path(key);p.parent.mkdir(parents=True,exist_ok=True)
        temporary=p.with_name(p.name+'.'+core.uuid.uuid4().hex+'.tmp')
        temporary.write_bytes(gzip.compress(core.dumps(value).encode(),mtime=0));temporary.replace(p)
    def create_json(self,key,value):
        p=self.path(key);p.parent.mkdir(parents=True,exist_ok=True)
        try:
            with p.open('xb') as f:f.write(gzip.compress(core.dumps(value).encode(),mtime=0))
            return True
        except FileExistsError:return False
    def list_keys(self,prefix):
        p=self.path(prefix)
        return [str(f.relative_to(core.DATA/'health-store')) for f in p.rglob('*.gz')] if p.exists() else []

def cached(store,key,ttl=30):
    identity=(getattr(store,'base','local'),key)
    with LOCK:
        old=CACHE.get(identity)
        if old and time.monotonic()-old[0]<ttl:return copy.deepcopy(old[1])
    value=store.get_json(key,optional=True)
    with LOCK:
        CACHE[identity]=(time.monotonic(),value)
        while len(CACHE)>64:CACHE.pop(min(CACHE,key=lambda k:CACHE[k][0]))
    return copy.deepcopy(value)

def invalidate():
    with LOCK:CACHE.clear()

def bootstrap_local(store):
    """Explicit offline audit, never called by a page request. Only dates are read."""
    started=time.monotonic();index={};calendar=core.calendar_dates()
    with core.db() as conn:
        instruments={r['symbol']:dict(r) for r in conn.execute('select * from instruments')}
        for symbol,item in instruments.items():
            present=[r[0][:10] for r in conn.execute("select event_time from observations where symbol=? and interval='1d' and event_time>=? order by event_time",(symbol,core.history_start()))]
            result=checks.gaps(present,calendar)
            index[symbol]=dict(name=item['name'],first_date=min(present,default=None),last_date=max(present,default=None),
                missing_dates=result['missing'],history_rows=len(present),audit_at=core.iso(),audit_source='SQLite observations / primary archive')
    store.put_json(INDEX,index)
    return dict(symbols=len(index),elapsed_seconds=round(time.monotonic()-started,3))

def record(index,symbol,result,present=None,calendar=None):
    item=index.setdefault(symbol,dict(name=symbol,missing_dates=[]))
    old_last=item.get('audited_through') or item.get('last_date')
    for k in ('last_date','last_failure','last_success','consecutive_failures','error_category','error_message','duration_seconds'):
        if k in result:item[k]=result[k]
    if present:
        present=set(present);first=min(filter(None,[item.get('first_date'),min(present)]),default=None)
        last=max(filter(None,[item.get('last_date'),max(present)]),default=None)
        missing=set(item.get('missing_dates',[]))-present
        # Existing internal holes are retained until a real accepted observation closes them.
        if calendar and old_last:missing.update(d for d in calendar if old_last<d<=last and d not in present)
        item.update(first_date=first,last_date=last,missing_dates=sorted(missing),audited_through=last,audit_at=core.iso())
    if result.get('requested_start') and result.get('error_category'):
        item.setdefault('gap_causes',{}).update({d:'采集失败（'+checks.ERROR_LABELS[result['error_category']]+ '）' for d in item.get('missing_dates',[]) if result['requested_start']<=d<=result['requested_end']})
    if result.get('missing_dates'):
        item['missing_dates']=sorted(set(item.get('missing_dates',[]))|set(result['missing_dates']))
        item.setdefault('gap_causes',{}).update({d:'数据源未提供（未证明停牌）' for d in result['missing_dates']})

def publish(store,state,index,at=None):
    at=(at or core.now()).astimezone(core.SH)
    config=store.get_json(CONFIG,optional=True) or {};evidence=store.get_json(EVIDENCE,optional=True) or {}
    snapshot=checks.assess(index,state,core.calendar_dates(),at,config,evidence)
    snapshot['sources']=state.get('sources',{})
    snapshot['slowest']=sorted(state.get('events',[]),key=lambda r:r.get('duration_seconds',0),reverse=True)[:10]
    snapshot['calendar_days']=core.calendar_dates()
    snapshot['status_source']=store.get_json(PREFIX+'status-source/'+str(snapshot['target_date'])+'.json.gz',optional=True)
    for name,s in snapshot['sources'].items():
        n=s.get('attempts',0);rate=s.get('successes',0)/n if n else None;s['success_rate']=rate;s['average_seconds']=s.get('seconds',0)/n if n else None
        if n>=snapshot['thresholds']['source_min_samples'] and (rate<snapshot['thresholds']['source_success_min'] or s.get('empty',0)/n>snapshot['thresholds']['source_empty_max']):
            snapshot['rules'].append(dict(key='source_'+name,level='attention',message=name+' 数据源成功率/空数据比例触发阈值',view='sources'))
            if snapshot['state']=='normal':snapshot['state']='attention'
    snapshot['runs']=state.get('runs',[])
    snapshot['repairs']=state.get('repairs',[])
    snapshot['storage']=store.get_json(PREFIX+'storage.json.gz',optional=True) or dict(available=False,reason='尚未完成存储清点；云端容量配额未知，不估算剩余天数')
    if (snapshot.get('status_source') or {}).get('status')=='failed':
        snapshot['rules'].append(dict(key='status_source',level='attention',message='停复牌证据来源本次失败，正常缺失排除可能不完整',view='sources'))
        if snapshot['state']=='normal':snapshot['state']='attention'
    snapshot['calendar']['holiday_source']=checks.HOLIDAY_SOURCE
    snapshot['storage'].pop('objects',None)
    snapshot['calendar']['source']='AKShare / 新浪历史交易日历（现有采集器）'
    snapshot['calendar']['holiday_validation']='已核对上交所2026年休市安排；其余年份未独立核验，周末检查覆盖全部日历'
    snapshot['assessment_at']=snapshot['generated_at'];snapshot['generated_at']=core.iso()
    store.put_json(INDEX,index);store.put_json(SNAPSHOT,snapshot)
    overview={k:v for k,v in snapshot.items() if k not in ('rows','calendar_days')}
    overview['calendar_days']=[d for d in snapshot['calendar_days'] if d>=(at-timedelta(days=31)).date().isoformat()]
    overview['initial_failures']=checks.select(snapshot['rows'],'failed')[:snapshot['thresholds']['page_size']]
    store.put_json(OVERVIEW,overview)
    return snapshot

def read_snapshot(store,detail=False):
    from fastapi import HTTPException
    snapshot=cached(store,SNAPSHOT if detail else OVERVIEW)
    if not snapshot and not detail:snapshot=cached(store,SNAPSHOT)
    if not snapshot:raise HTTPException(503,'健康快照尚未生成，请先运行健康审计任务')
    age=checks.elapsed(snapshot['generated_at'],core.now())
    snapshot['statistics_age_seconds']=age
    from . import daily_sync
    live=cached(store,daily_sync.STATUS) or {}
    at=core.now().astimezone(core.SH);invocation=live.get('invocation',{})
    idle=checks.elapsed(invocation.get('heartbeat'),at);duration=checks.elapsed(invocation.get('started_at'),at)
    if invocation.get('status')=='running' and ((idle or 0)>snapshot['thresholds']['heartbeat_minutes']*60 or (duration or 0)>snapshot['thresholds']['invocation_max_seconds']):
        snapshot['rules'].append(dict(key='live_stalled',level='error',message=f'任务运行超时或停滞，最后进度距今 {round((idle or 0)/60,1)} 分钟',view='tasks'));snapshot['state']='error'
    snapshot['run']['invocation']=invocation or snapshot['run'].get('invocation',{})
    expected=daily_sync.freshness(None,snapshot.get('calendar_days') or core.calendar_dates(),at)['expected_date']
    if expected!=snapshot['target_date']:
        deadline=datetime.fromisoformat(expected+'T17:30:00').replace(tzinfo=at.tzinfo) if expected else None
        overdue=deadline is None or (at-deadline).total_seconds()>snapshot['thresholds']['schedule_grace_minutes']*60
        level='error' if overdue else 'attention'
        snapshot['rules'].append(dict(key='target_changed',level=level,message='应更新日已变化或日历覆盖不足，现有统计尚未更新',view='tasks'))
        if overdue or snapshot['state']=='normal':snapshot['state']=level
    local=core.now().astimezone(core.SH)
    active=7<=local.hour<10 or local.hour*60+local.minute>=1050
    if age is not None and active and age>snapshot['thresholds']['snapshot_max_minutes']*60:
        snapshot['rules'].append(dict(key='snapshot_old',level='attention',message='健康统计已过期，不能据此确认系统正常',view='tasks'))
        if snapshot['state']=='normal':snapshot['state']='attention'
    return snapshot

def request_repair(store,symbols,start,end,user,target,retry_for=None):
    from fastapi import HTTPException
    symbols=sorted(set(symbols))
    identity=dict(symbols=symbols,start=start,end=end,target=target)
    if retry_for:identity['retry_for']=retry_for
    request_id=hashlib.sha256(core.dumps(identity).encode()).hexdigest()[:32]
    key=QUEUE+request_id+'.json.gz'
    previous=store.get_json(key,optional=True)
    if previous:return dict(id=request_id,duplicate=True,message='相同范围已有请求，返回原任务',request=previous)
    # Worker state is read uncached: an already running symbol cannot be submitted again.
    state=store.get_json('system/daily-sync/state.json.gz',optional=True) or {}
    running=set(state.get('repair_running',[]))
    overlap=running.intersection(symbols)
    if overlap:raise HTTPException(409,'已有补采任务运行：'+', '.join(sorted(overlap)))
    request=dict(id=request_id,**identity,requested_by=user,requested_at=core.iso(),status='queued')
    created=store.create_json(key,request)
    invalidate()
    return dict(id=request_id,duplicate=not created,message='已排队；定时采集窗口内按限流规则处理',request=request)

def process_repairs(store,state,index,catalog,calendar,budget_seconds=40):
    """Only the slot-claimed daily worker calls this. Immutable requests, persistent results.

    Batches may overlap, but a successful symbol/range has one immutable execution key;
    restarted workers re-read results. Data writes are date-keyed and replay-safe.
    """
    from . import tencent_source,daily_sync
    p=checks.policy(store.get_json(CONFIG,optional=True) or {});started=time.monotonic();processed=0
    evidence=store.get_json(EVIDENCE,optional=True) or {}
    keys=store.list_keys(QUEUE)
    recent=[];completed=set(state.get('repair_completed',[]))
    for key in sorted(keys):
        if key.removeprefix(QUEUE).removesuffix('.json.gz') in completed:continue
        if time.monotonic()-started>=budget_seconds:break
        request=store.get_json(key);result_key=RESULTS+request['id']+'.json.gz'
        result=store.get_json(result_key,optional=True) or dict(id=request['id'],requested_by=request['requested_by'],requested_at=request['requested_at'],start=request['start'],end=request['end'],total=len(request['symbols']),items={},status='queued')
        if result['status']=='complete':completed.add(request['id']);recent.append(result);continue
        for symbol in request['symbols']:
            if symbol in result['items']:continue
            if processed>=p['repair_batch_limit'] or time.monotonic()-started>=budget_seconds:break
            state['repair_running']=[symbol];store.put_json(daily_sync.STATE,state)
            execution=hashlib.sha256(core.dumps(dict(symbol=symbol,start=request['start'],end=request['end'],target=request['target'],retry_for=request.get('retry_for'))).encode()).hexdigest()
            execution_key=PREFIX+'executions/'+execution+'.json.gz'
            done=store.get_json(execution_key,optional=True)
            if done:out=dict(done,deduplicated=True)
            elif result.get('attempts',{}).get(symbol,0)>=4:
                out=dict(status='failed',finished_at=core.iso(),error_category='timeout',error_message='补采连续中断达到4次，保留旧行情；请核对源或缩小区间')
                store.put_json(execution_key,out)
            else:
                result.setdefault('attempts',{})[symbol]=result.get('attempts',{}).get(symbol,0)+1
                result.update(status='running',updated_at=core.iso())
                store.put_json(result_key,result)
                tick=time.monotonic()
                try:
                    entry=catalog.get('entries',{}).get(symbol+'/1d');base=None
                    if entry:base=store.get_json(entry['chunk']).get(symbol+'/1d')
                    previous=daily_sync.merge_history(base,store.get_json('scheduled/'+symbol+'/1d.json.gz',optional=True),store.get_json('live/'+symbol+'/1d.json.gz',optional=True))
                    updated=tencent_source.refresh_series(symbol,'1d',previous,store,start=request['start'],end=request['end'],output_prefix='scheduled',allowed_dates=set(calendar))
                    present={r['event_time'][:10] for r in updated['records']}
                    holes=checks.gaps(present,calendar)
                    out=dict(status='success',finished_at=core.iso(),last_date=max(present),missing_dates=holes['missing'],duration_seconds=round(time.monotonic()-tick,3))
                    record(index,symbol,out,present,calendar)
                    store.put_json(INDEX,index)  # Confirm the coverage change before durable execution acknowledgement.
                    # A complete range is proven from accepted observations, not HTTP success.
                    unresolved=[d for d in calendar if request['start']<=d<=request['end'] and d not in present and not checks.normal_reason(d,evidence.get(symbol))]
                    out['unresolved_dates']=unresolved
                    if unresolved:out['status']='incomplete'
                    elif max(present)>=state.get('target','9999') and symbol in state.get('jobs',{}):
                        state['jobs'][symbol].update(status='current',last_date=max(present),last_success=core.iso(),consecutive_failures=0,error=None,error_category=None)
                except Exception as exc:out=dict(status='failed',finished_at=core.iso(),duration_seconds=round(time.monotonic()-tick,3),**checks.error_info(exc,(getattr(store,'key',''),)))
                source=state.setdefault('sources',{}).setdefault('Tencent',dict(attempts=0,successes=0,seconds=0))
                source['attempts']+=1;source['seconds']+=out.get('duration_seconds',0)
                if out['status']=='success':source['successes']+=1
                else:
                    category=out.get('error_category','incomplete');source[category]=source.get(category,0)+1
                state.setdefault('events',[]).append(dict(symbol=symbol,at=core.iso(),operation='manual_repair',request_id=request['id'],status=out['status'],duration_seconds=out.get('duration_seconds',0),error_category=out.get('error_category'),error_message=out.get('error_message')))
                store.put_json(execution_key,out)
                wait=p['repair_min_interval_seconds']-(time.monotonic()-tick)
                if wait>0:time.sleep(wait)
            result['items'][symbol]=out;processed+=1;result['status']='running';result['updated_at']=core.iso()
            store.put_json(result_key,result)
            state['repair_running']=[];store.put_json(daily_sync.STATE,state)
        if len(result['items'])==result['total']:result['status']='complete';result['finished_at']=core.iso();store.put_json(result_key,result);completed.add(request['id'])
        recent.append(result)
        if processed>=p['repair_batch_limit'] or time.monotonic()-started>=budget_seconds:break
    state['repair_completed']=sorted(completed)
    state['repairs']=[{k:v for k,v in r.items() if k!='items'}|dict(processed=len(r['items']),failed=sum(i['status']=='failed' for i in r['items'].values()),incomplete=sum(i['status']=='incomplete' for i in r['items'].values())) for r in recent[-p['recent_runs']:]]
    return processed

def routes(store_factory,user_for,check_action,page=None):
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
    class RepairBody(BaseModel):
        symbols:list[str]=Field(default_factory=list,max_length=10000)
        view:str='';query:str='';reason:str=''
        start:str|None=None;end:str|None=None
        lag_min:int|None=None;lag_max:int|None=None
    router=APIRouter()
    @router.get('/data-health')
    def health_page():return page() if page else FileResponse(core.ROOT/'app/static/data-health/index.html')
    @router.get('/api/data-health')
    def overview():
        s=read_snapshot(store_factory());s.pop('rows',None);s.pop('calendar_days',None);s['storage'].pop('objects',None);return s
    @router.get('/api/data-health/securities')
    def securities(view:str='all',q:str='',reason:str='',sort:str='failures',page:int=1,size:int|None=None,lag_min:int|None=None,lag_max:int|None=None):
        s=read_snapshot(store_factory(),detail=True)
        try:items=checks.select(s['rows'],view,q,reason,sort,s['thresholds']['stale_warn_sessions'])
        except ValueError as exc:raise HTTPException(400,str(exc))
        if lag_min is not None:items=[r for r in items if not r['excluded'] and r['lag_sessions'] is not None and r['lag_sessions']>=lag_min]
        if lag_max is not None:items=[r for r in items if r['lag_sessions'] is not None and r['lag_sessions']<=lag_max]
        size=max(1,min(size or s['thresholds']['page_size'],200));page=max(1,page)
        return dict(total=len(items),page=page,size=size,items=items[(page-1)*size:page*size],generated_at=s['generated_at'])
    @router.post('/api/data-health/repair')
    def repair(body:RepairBody,request:Request):
        check_action(request);store=store_factory();s=read_snapshot(store,detail=True)
        symbols=body.symbols
        if body.view:
            try:
                selected=checks.select(s['rows'],'all' if body.lag_min is not None else body.view,body.query,body.reason,stale_threshold=s['thresholds']['stale_warn_sessions'])
                if body.lag_min is not None:selected=[r for r in selected if not r['excluded'] and r['lag_sessions'] is not None and r['lag_sessions']>=body.lag_min]
                if body.lag_max is not None:selected=[r for r in selected if r['lag_sessions'] is not None and r['lag_sessions']<=body.lag_max]
                symbols=[r['symbol'] for r in selected]
            except ValueError as exc:raise HTTPException(400,str(exc))
        known={r['symbol'] for r in s['rows']}
        if not symbols or any(x not in known for x in symbols):raise HTTPException(400,'请选择有效证券')
        start=body.start or core.history_start();end=body.end or s['target_date']
        try:
            if not end or datetime.fromisoformat(start).date().isoformat()!=start or datetime.fromisoformat(end).date().isoformat()!=end or not core.history_start()<=start<=end<=s['target_date']:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,'补采区间必须在近五年及应有交易日以内')
        return request_repair(store,symbols,start,end,user_for(request),s['target_date'])
    @router.get('/api/data-health/repairs')
    def repairs(page:int=1,size:int=20):
        store=store_factory();items=[];keys=cached_requests(store);page=max(1,page);size=max(1,min(50,size))
        for key in keys[(page-1)*size:page*size]:
            req=cached(store,key);result=cached(store,RESULTS+req['id']+'.json.gz')
            if result:
                results=result.pop('items',{})
                result.update(processed=len(results),failed=sum(x['status']=='failed' for x in results.values()),incomplete=sum(x['status']=='incomplete' for x in results.values()))
            items.append(dict(req,symbols=req['symbols'][:20],total_symbols=len(req['symbols']),result=result))
        return dict(items=items,total=len(keys),page=page)
    @router.post('/api/data-health/repairs/{request_id}/retry')
    def retry_repair(request_id:str,request:Request):
        import re
        check_action(request)
        if not re.fullmatch('[0-9a-f]{32}',request_id):raise HTTPException(404,'任务不存在')
        store=store_factory();original=store.get_json(QUEUE+request_id+'.json.gz',optional=True);result=store.get_json(RESULTS+request_id+'.json.gz',optional=True)
        if not original:raise HTTPException(404,'任务不存在')
        if not result or result.get('status')!='complete':raise HTTPException(409,'原任务尚未完成，不能重复执行')
        symbols=[symbol for symbol,out in result['items'].items() if out['status'] in ('failed','incomplete')]
        if not symbols:raise HTTPException(400,'原任务没有需要重试的证券')
        target=read_snapshot(store)['target_date']
        return request_repair(store,symbols,original['start'],original['end'],user_for(request),target,retry_for=request_id)
    @router.get('/api/data-health/repairs/{request_id}')
    def repair_items(request_id:str,page:int=1,size:int=50):
        import re
        if not re.fullmatch('[0-9a-f]{32}',request_id):raise HTTPException(404,'任务不存在')
        store=store_factory();req=cached(store,QUEUE+request_id+'.json.gz')
        if not req:raise HTTPException(404,'任务不存在')
        result=cached(store,RESULTS+request_id+'.json.gz') or {};page=max(1,page);size=max(1,min(200,size))
        return dict(id=request_id,total=len(req['symbols']),page=page,items=[dict(symbol=s,**result.get('items',{}).get(s,{'status':'queued'})) for s in req['symbols'][(page-1)*size:page*size]])
    @router.get('/api/data-health/storage')
    def storage(category:str='',page:int=1,size:int=50):
        value=cached(store_factory(),PREFIX+'storage.json.gz') or {}
        items=[r for r in value.get('objects',[]) if not category or r['key'].split('/')[0]==category]
        page=max(1,page);size=min(200,max(1,size))
        return dict(total=len(items),items=items[(page-1)*size:page*size],page=page,generated_at=value.get('generated_at'))
    @router.get('/api/data-health/events')
    def events(reason:str='',symbol:str='',page:int=1,size:int=50):
        state=cached(store_factory(),'system/daily-sync/state.json.gz') or {}
        items=[r for r in state.get('events',[]) if (not reason or r.get('error_category')==reason) and (not symbol or r['symbol']==symbol)]
        page=max(1,page);size=min(200,max(1,size))
        return dict(total=len(items),items=items[::-1][(page-1)*size:page*size],page=page)
    @router.get('/api/data-health/config')
    def config():return checks.policy(cached(store_factory(),CONFIG))
    @router.put('/api/data-health/config')
    def set_config(body:dict,request:Request):
        check_action(request)
        if user_for(request) not in ('owner','local'):raise HTTPException(403,'仅管理员可修改阈值')
        try:p=checks.policy(body)
        except ValueError as exc:raise HTTPException(400,str(exc))
        store=store_factory();store.put_json(CONFIG,p)
        store.put_json(PREFIX+'audit/'+core.uuid.uuid4().hex+'.json.gz',dict(actor=user_for(request),at=core.iso(),action='thresholds',values=p));invalidate()
        return dict(thresholds=p,message='已保存，下次健康快照生成时生效')
    return router

def cached_requests(store):
    identity=(getattr(store,'base','local'),'requests-list')
    with LOCK:
        old=CACHE.get(identity)
        if old and time.monotonic()-old[0]<30:return old[1]
    keys=store.list_keys(QUEUE)
    with LOCK:
        CACHE[identity]=(time.monotonic(),keys)
        while len(CACHE)>64:CACHE.pop(min(CACHE,key=lambda k:CACHE[k][0]))
    return keys

def scan_storage_page(store,target):
    """One listing page per invocation; publish only a complete bucket inventory.

    Listing is a dated observation, not a transactionally frozen bucket snapshot.
    No hypothetical quota or remaining disk lifetime is fabricated.
    """
    if not hasattr(store,'_request'):return
    key=PREFIX+'storage-scan.json.gz';scan=store.get_json(key,optional=True) or {}
    if scan.get('target')!=target:scan=dict(target=target,cursor=None,objects={},started_at=core.iso())
    if scan.get('complete'):return
    body=dict(prefix='',limit=1000,with_delimiter=False,sortBy=dict(column='name',order='asc'))
    if scan['cursor']:body['cursor']=scan['cursor']
    page=store._request('POST','/v1/storages/object/list/'+store.bucket,json=body).json()
    for item in page.get('objects',[]):
        name=item.get('key') or item['name'];scan['objects'][name]=int(item.get('metadata',{}).get('size',0))
    if page.get('hasNext'):
        cursor=page.get('nextCursor')
        if not cursor or cursor==scan['cursor']:raise RuntimeError('Storage scan pagination cursor missing')
        scan['cursor']=cursor
    else:
        scan['complete']=True;categories={}
        for name,size in scan['objects'].items():
            c=categories.setdefault(name.split('/')[0],dict(bytes=0,objects=0));c['bytes']+=size;c['objects']+=1
        previous=store.get_json(PREFIX+'storage.json.gz',optional=True) or {};total=sum(scan['objects'].values());history=(previous.get('history',[])+[dict(at=core.iso(),bytes=total)])[-30:]
        age=checks.elapsed(history[0]['at'],core.now());growth=(total-history[0]['bytes'])/(age/86400) if age and age>=86400 else None
        store.put_json(PREFIX+'storage.json.gz',dict(available=True,generated_at=core.iso(),scan_started_at=scan['started_at'],total_bytes=total,categories=categories,history=history,daily_growth_bytes=growth,capacity_bytes=None,remaining_days=None,reason='每日分批清点；非事务一致快照，容量配额未知。history 含日线和少量分钟归档，尚不拆分混合文件字节。',objects=[dict(key=k,bytes=v) for k,v in scan['objects'].items()]))
    store.put_json(key,scan)
