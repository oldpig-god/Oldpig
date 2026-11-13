"""Bounded single-writer daily ingestion. Durable progress; no browser dependency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import gzip
import time
from . import core,tencent_source,data_health,health_service

STATE='system/daily-sync/state.json.gz'
STATUS='system/daily-sync/status.json.gz'

def merge_history(*items):
    rows={};batch=None
    for item in items:
        if not item:continue
        for row in item.get('records',[]):
            key=row['event_time']
            if key>=core.history_start() and (key not in rows or row.get('fetched_at','')>=rows[key].get('fetched_at','')):rows[key]=row
        candidate=item.get('last_batch')
        if candidate and (not batch or candidate.get('finished_at','')>batch.get('finished_at','')):batch=candidate
    return dict(records=[rows[k] for k in sorted(rows)],last_batch=batch)

def freshness(last,calendar,at=None):
    at=(at or core.now()).astimezone(core.SH);day=at.date().isoformat()
    if not calendar or not calendar[0]<=day<=calendar[-1]:
        return dict(state='unknown',expected_date=None,last_date=last,lag_sessions=None,label='交易日历未覆盖，无法核验时效')
    eligible=[d for d in calendar if d<day or (d==day and at.hour*60+at.minute>=1050)]
    target=eligible[-1] if eligible else None
    lag=sum(1 for d in eligible if last and d>last) if last else None
    return dict(state='missing' if not last else 'current' if target and last>=target else 'stale',expected_date=target,last_date=last,lag_sessions=lag,
                label='暂无日线' if not last else '已覆盖应有交易日' if target and last>=target else '数据滞后',cutoff='交易日17:30后要求当日日线；此前使用上一交易日')

def summary(state):
    jobs=state['jobs'];counts={k:sum(j['status']==k for j in jobs.values()) for k in ['pending','current','retry','exhausted','skipped','running']}
    return dict(target_date=state['target'],updated_at=core.iso(),total=len(jobs),counts=counts,
                complete=counts['current']+counts['skipped']==len(jobs),invocation=state.get('invocation'),last_run=state.get('last_run'),calendar_end=state.get('calendar_end'),
                policy='收盘17:30后分批更新；失败15/60/180分钟后重试，每个目标日最多4次。停牌或源未覆盖不伪造日线。')

def initialize(store):
    current=store.get_json('system/current.json.gz')
    core.DATA.mkdir(parents=True,exist_ok=True)
    if not (core.DATA/'workbench.sqlite').exists():
        (core.DATA/'workbench.sqlite').write_bytes(gzip.decompress(store.get(current['seed'])))
    core.init()
    return store.get_json(current['catalog'])

def run(store,max_symbols=160,budget_seconds=110,at=None,clock=time.monotonic):
    invocation_tick=time.monotonic()
    health_policy=data_health.policy(store.get_json(health_service.CONFIG,optional=True) or {})
    at=at or core.now();calendar=core.calendar_dates();fresh=freshness(None,calendar,at);target=fresh['expected_date']
    if not target:
        status=dict(state='calendar_unknown',updated_at=core.iso(),message=fresh['label']);store.put_json(STATUS,status);return status
    state=store.get_json(STATE,optional=True)
    if not state or state['target']!=target:
        old=state or {};old_jobs=old.get('jobs',{});runs=old.get('runs',[])
        if old.get('target'):
            archive=dict(target=old['target'],started_at=old.get('started_at'),finished_at=old.get('finished_at'),counts=summary(old)['counts'],duration_seconds=old.get('duration_seconds',0),stages=old.get('stages',{}),slowest=sorted(old.get('events',[]),key=lambda r:r.get('duration_seconds',0),reverse=True)[:10])
            runs=(runs+[archive])[-health_policy['recent_runs']:]
            store.put_json(health_service.PREFIX+'runs/'+old['target']+'.json.gz',archive)
        # Progress belongs to a target day. Every symbol is revisited on the next target;
        # its durable delta watermark determines how much a previous failure must catch up.
        known=core.universe();priority=['000034.SZ','002463.SZ','002384.SZ','603256.SH','002938.SZ','002484.SZ','600031.SH','510300.SH']
        order=[s for s in priority if s in known]+[s for s in sorted(known) if s not in priority]
        state=dict(target=target,calendar_end=calendar[-1],started_at=core.iso(),runs=runs,sources={},repair_completed=old.get('repair_completed',[]),jobs={s:dict(status='pending',attempts=0,**{k:old_jobs.get(s,{}).get(k) for k in ('last_success','last_failure','consecutive_failures')}) for s in order})
        store.put_json(STATE,state)
    # A previous invocation killed by the platform cannot report its exception. Recover
    # its persisted active group as timeouts before claiming new work; attempts are
    # persisted BEFORE network calls, so poison securities cannot loop forever.
    for symbol in state.get('invocation',{}).get('active_symbols',[]):
        job=state['jobs'].get(symbol)
        if job and job.get('status')=='running':
            job.update(status='exhausted' if job.get('attempts',0)>=4 else 'retry',error='WorkerInterrupted',error_category='timeout',error_message='上次工作进程未提交结果（超时或中断），保留旧数据',last_failure=core.iso(),consecutive_failures=(job.get('consecutive_failures') or 0)+1,next_attempt=at.timestamp()+900)
    health_index=store.get_json(health_service.INDEX,optional=True) or {}
    evidence=store.get_json(health_service.EVIDENCE,optional=True) or {}
    for symbol,item in core.universe().items():health_index.setdefault(symbol,dict(name=item.get('name',symbol),missing_dates=[],last_date=(CATALOG.get('entries',{}).get(symbol+'/1d',{}).get('end') or '')[:10] or None,audit_source='catalog_only'))
    for symbol,job in state['jobs'].items():
        if job.get('accepted_dates') and job.get('checked_at','')>health_index.get(symbol,{}).get('audit_at',''):
            health_service.record(health_index,symbol,job,job['accepted_dates'],calendar)
        reason=data_health.normal_reason(target,evidence.get(symbol))
        if reason:job.update(status='skipped',skip_reason=reason)
    state['invocation']=dict(status='running',started_at=core.iso(),heartbeat=core.iso(),processed=0)
    store.put_json(STATE,state);store.put_json(STATUS,summary(state))
    health_service.publish(store,state,health_index,at)
    now=at.timestamp();jobs=state['jobs'];due=[s for s,j in jobs.items() if j['status'] in ('pending','retry') and j.get('next_attempt',0)<=now][:max_symbols]
    started=clock();processed=0
    def update(symbol):
        tick=time.monotonic()
        try:
            key='scheduled/'+symbol+'/1d.json.gz';previous=store.get_json(key,optional=True) or {'records':[]}
            existing=[r['event_time'][:10] for r in previous['records']]
            # First run catches the archive tail; later runs overlap five sessions for revisions.
            baseline=previous.get('base_end')
            if baseline is None:
                entry=CATALOG.get('entries',{}).get(symbol+'/1d')
                baseline=entry['end'][:10] if entry else core.history_start()
            watermark=max(existing,default=baseline)
            missing=previous.get('missing_dates',[])
            anchor=min([watermark]+missing)
            start=max(core.history_start(),(datetime.fromisoformat(anchor)-timedelta(days=10)).date().isoformat())
            updated=tencent_source.refresh_series(symbol,'1d',previous,store,start=start,end=target,output_prefix='scheduled',allowed_dates=set(calendar))
            present={r['event_time'][:10] for r in updated['records']};last=max(present,default=baseline)
            gaps=[d for d in calendar if baseline<d<=target and d not in present and not data_health.normal_reason(d,evidence.get(symbol))]
            updated.update(base_end=baseline,missing_dates=gaps,checked_at=core.iso(),target_date=target)
            # Commit status only after durable data. A timeout before progress save merely replays.
            store.put_json(key,updated)
            return dict(duration_seconds=round(time.monotonic()-tick,3),present_dates=sorted(present),last_date=last,missing_dates=gaps,status='current' if last>=target and not gaps else 'retry',error=None if last>=target and not gaps else 'source_incomplete_or_suspended')
        except Exception as exc:
            # Never persist provider URLs, credentials or raw exception text in public status.
            return dict(status='retry',error=type(exc).__name__,requested_start=locals().get('start'),requested_end=target,duration_seconds=round(time.monotonic()-tick,3),**data_health.error_info(exc,(getattr(store,'key',''),)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0,len(due),4):
            if clock()-started>=budget_seconds:break
            group=due[offset:offset+4]
            state['invocation'].update(active_symbols=group,heartbeat=core.iso())
            for symbol in group:jobs[symbol].update(status='running',attempts=jobs[symbol]['attempts']+1)
            store.put_json(STATE,state)
            for symbol,result in zip(group,pool.map(update,group)):
                present=result.pop('present_dates',None)
                if present:result['accepted_dates']=present
                job=jobs[symbol];job.update(result);job['checked_at']=core.iso()
                if job['status']=='current':job.update(last_success=core.iso(),consecutive_failures=0)
                else:
                    job.update(last_failure=core.iso(),consecutive_failures=(job.get('consecutive_failures') or 0)+1)
                    if not result.get('error_category'):job['error_category']='incomplete'
                state.setdefault('events',[]).append(dict(symbol=symbol,at=core.iso(),status=job['status'],error_category=job.get('error_category') if job['status']!='current' else None,duration_seconds=result.get('duration_seconds',0),error_message=job.get('error_message') if job['status']!='current' else None,attempt=job['attempts']))
                health_service.record(health_index,symbol,result,present,calendar)
                health_index[symbol].update({k:job.get(k) for k in ('last_success','last_failure','consecutive_failures')})
                source=state.setdefault('sources',{}).setdefault('Tencent',dict(attempts=0,successes=0,seconds=0))
                source['attempts']+=1;source['seconds']+=result.get('duration_seconds',0)
                if job['status']=='current':source['successes']+=1
                else:source[job['error_category']]=source.get(job['error_category'],0)+1
                if job['status']=='retry':
                    if job['attempts']>=4:job['status']='exhausted'
                    else:job['next_attempt']=now+[900,3600,10800][job['attempts']-1]
                processed+=1
            state['last_run']=dict(at=core.iso(),processed=processed)
            state['invocation'].update(heartbeat=core.iso(),processed=processed,active_symbols=[])
            store.put_json(STATE,state);store.put_json(STATUS,summary(state))
    daily_seconds=round(time.monotonic()-invocation_tick,3)
    repaired=0
    if hasattr(store,'list_keys'):
        repaired=health_service.process_repairs(store,state,health_index,CATALOG,calendar,budget_seconds=max(0,150-daily_seconds))
    state['invocation'].update(status='complete',finished_at=core.iso(),heartbeat=core.iso(),processed=processed)
    duration=round(time.monotonic()-invocation_tick,3)
    if processed or repaired:state['duration_seconds']=state.get('duration_seconds',0)+duration
    stages=state.setdefault('stages',dict(daily_seconds=0,repair_seconds=0))
    if processed:stages['daily_seconds']+=daily_seconds
    if repaired:stages['repair_seconds']+=round(duration-daily_seconds,3)
    if state.get('started_at') and not state.get('finished_at') and all(j['status'] in ('current','skipped','exhausted') for j in jobs.values()):state['finished_at']=core.iso()
    result=summary(state);store.put_json(STATE,state);store.put_json(STATUS,result)
    if time.monotonic()-invocation_tick<150:
        try:health_service.scan_storage_page(store,target)
        except Exception as exc:state['storage_scan_error']=data_health.error_info(exc,(getattr(store,'key',''),));store.put_json(STATE,state)
    health_service.publish(store,state,health_index,core.now() if at is None else at)
    return result

CATALOG={}
def main(event,context):
    # Atomic five-minute slot claim + 240s platform timeout prevent overlapping writers.
    from .cloud_store import CloudStore
    local=core.now().astimezone(core.SH)
    manual=isinstance(event,dict) and event.get('action')=='health_repair'
    if not manual and not (7<=local.hour<10 or local.hour*60+local.minute>=1050):return {'state':'outside_window'}
    store=CloudStore()
    config=store.get_json('system/daily-sync/config.json.gz',optional=True) or {}
    if not config.get('enabled'):return {'state':'disabled'}
    stamp=core.now().timestamp()
    if stamp%300>45:return {'state':'deferred_late_delivery'}
    if not store.claim('system/daily-sync/claims/'+str(int(stamp//300))):return {'state':'claim_not_acquired'}
    if core.now().timestamp()//300!=stamp//300 or core.now().timestamp()%300>45:return {'state':'deferred_slow_claim'}
    CATALOG.clear();CATALOG.update(initialize(store))
    from . import health_status_source
    target=freshness(None,core.calendar_dates())['expected_date']
    if target:health_status_source.refresh(store,target)
    return run(store,max_symbols=0,budget_seconds=0) if manual else run(store)
