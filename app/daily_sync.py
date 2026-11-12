"""Bounded single-writer daily ingestion. Durable progress; no browser dependency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import gzip
import time
from . import core,tencent_source

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
    jobs=state['jobs'];counts={k:sum(j['status']==k for j in jobs.values()) for k in ['pending','current','retry','exhausted']}
    return dict(target_date=state['target'],updated_at=core.iso(),total=len(jobs),counts=counts,
                complete=counts['current']==len(jobs),last_run=state.get('last_run'),calendar_end=state.get('calendar_end'),
                policy='收盘17:30后分批更新；失败15/60/180分钟后重试，每个目标日最多4次。停牌或源未覆盖不伪造日线。')

def initialize(store):
    current=store.get_json('system/current.json.gz')
    core.DATA.mkdir(parents=True,exist_ok=True)
    if not (core.DATA/'workbench.sqlite').exists():
        (core.DATA/'workbench.sqlite').write_bytes(gzip.decompress(store.get(current['seed'])))
    core.init()
    return store.get_json(current['catalog'])

def run(store,max_symbols=160,budget_seconds=110,at=None,clock=time.monotonic):
    at=at or core.now();calendar=core.calendar_dates();fresh=freshness(None,calendar,at);target=fresh['expected_date']
    if not target:
        status=dict(state='calendar_unknown',updated_at=core.iso(),message=fresh['label']);store.put_json(STATUS,status);return status
    state=store.get_json(STATE,optional=True)
    if not state or state['target']!=target:
        # Progress belongs to a target day. Every symbol is revisited on the next target;
        # its durable delta watermark determines how much a previous failure must catch up.
        known=core.universe();priority=['000034.SZ','002463.SZ','002384.SZ','603256.SH','002938.SZ','002484.SZ','600031.SH','510300.SH']
        order=[s for s in priority if s in known]+[s for s in sorted(known) if s not in priority]
        state=dict(target=target,calendar_end=calendar[-1],jobs={s:dict(status='pending',attempts=0) for s in order})
        store.put_json(STATE,state)
    now=at.timestamp();jobs=state['jobs'];due=[s for s,j in jobs.items() if j['status'] in ('pending','retry') and j.get('next_attempt',0)<=now][:max_symbols]
    started=clock();processed=0
    def update(symbol):
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
            gaps=[d for d in calendar if baseline<d<=target and d not in present]
            updated.update(base_end=baseline,missing_dates=gaps,checked_at=core.iso(),target_date=target)
            # Commit status only after durable data. A timeout before progress save merely replays.
            store.put_json(key,updated)
            return dict(last_date=last,missing_dates=gaps,status='current' if last>=target and not gaps else 'retry',error=None if last>=target and not gaps else 'source_incomplete_or_suspended')
        except Exception as exc:
            # Never persist provider URLs, credentials or raw exception text in public status.
            return dict(status='retry',error=type(exc).__name__)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0,len(due),4):
            if clock()-started>=budget_seconds:break
            group=due[offset:offset+4]
            for symbol,result in zip(group,pool.map(update,group)):
                job=jobs[symbol];job.update(result);job['attempts']+=1;job['checked_at']=core.iso()
                if job['status']=='retry':
                    if job['attempts']>=4:job['status']='exhausted'
                    else:job['next_attempt']=now+[900,3600,10800][job['attempts']-1]
                processed+=1
            state['last_run']=dict(at=core.iso(),processed=processed)
            store.put_json(STATE,state);store.put_json(STATUS,summary(state))
    result=summary(state);store.put_json(STATUS,result);return result

CATALOG={}
def main(event,context):
    # Atomic five-minute slot claim + 240s platform timeout prevent overlapping writers.
    from .cloud_store import CloudStore
    local=core.now().astimezone(core.SH)
    if not (7<=local.hour<10 or local.hour*60+local.minute>=1050):return {'state':'outside_window'}
    store=CloudStore()
    config=store.get_json('system/daily-sync/config.json.gz',optional=True) or {}
    if not config.get('enabled'):return {'state':'disabled'}
    stamp=core.now().timestamp()
    if stamp%300>45:return {'state':'deferred_late_delivery'}
    if not store.claim('system/daily-sync/claims/'+str(int(stamp//300))):return {'state':'claim_not_acquired'}
    if core.now().timestamp()//300!=stamp//300 or core.now().timestamp()%300>45:return {'state':'deferred_slow_claim'}
    CATALOG.clear();CATALOG.update(initialize(store))
    return run(store)
