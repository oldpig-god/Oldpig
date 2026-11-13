"""Pure health checks. Absence is NOT suspension without dated, sourced evidence."""
from datetime import datetime, date
import re, math
from bisect import bisect_right

DEFAULTS = dict(completion_min=.98, stale_warn_sessions=2, stale_error_sessions=5,
    calendar_warn_days=30, heartbeat_minutes=15, invocation_max_seconds=240,
    completion_grace_hours=6, schedule_grace_minutes=15, snapshot_max_minutes=20,
    source_min_samples=10, source_success_min=.8, source_empty_max=.2,
    repair_concurrency=2, repair_batch_limit=20, repair_min_interval_seconds=1,
    recent_runs=30, page_size=50)
# SSE annual holiday notice, verified 2026-11-13. Never extrapolated to other years.
HOLIDAYS_2026=[('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),('2026-06-19','2026-06-21'),('2026-09-25','2026-09-27'),('2026-10-01','2026-10-07')]
HOLIDAY_SOURCE='https://www.sse.com.cn/disclosure/dealinstruc/closed/'
ERROR_LABELS = dict(incomplete='数据区间不完整',timeout='网络超时', rate_limit='数据源限流', empty='数据源无返回',
    format='数据格式异常', validation='校验不通过', storage='存储写入失败', unknown='未知')

def policy(values=None):
    out=dict(DEFAULTS)
    for k,v in (values or {}).items():
        if k not in out or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError('无效阈值：'+k)
        if k in ('completion_min','source_success_min','source_empty_max') and v>1:raise ValueError('比例应小于等于1')
        if isinstance(DEFAULTS[k],int) and int(v)!=v:raise ValueError('阈值应为整数：'+k)
        out[k]=v
    if out['stale_warn_sessions']>out['stale_error_sessions']:raise ValueError('过期告警阈值顺序错误')
    if out['repair_concurrency']>4 or out['repair_batch_limit']>160 or out['page_size']>200:raise ValueError('超过资源安全上限')
    return out

def error_info(exc, secrets=()):
    raw=str(exc);name=type(exc).__name__;text=(name+' '+raw).lower()
    status=getattr(getattr(exc,'response',None),'status_code',None)
    category=('storage' if 'storage' in text else 'rate_limit' if status==429 or '429' in text or 'rate limit' in text
        else 'timeout' if 'timeout' in text or 'timed out' in text else 'empty' if 'empty' in text or 'no return' in text
        else 'validation' if any(s in text for s in ('valid','calendar','outside','duplicate'))
        else 'format' if any(s in text for s in ('schema','json','decode','format','keyerror')) else 'unknown')
    # Keep the original diagnostic text after secret/URL redaction, including unknown errors.
    for secret in secrets:
        if secret:raw=raw.replace(secret,'[redacted]')
    raw=re.sub(r'https?://\S+','[URL omitted]',raw)
    raw=re.sub(r'(?i)(authorization|apikey|api_key|token|password|secret)\s*[:=]\s*\S+',r'\1=[redacted]',raw)
    return dict(error_category=category,error_type=name,error_message=raw[:1000])

def normal_reason(day, evidence):
    if not evidence or not evidence.get('source') or not evidence.get('verified_at'):return None
    if evidence.get('listed_at') and day<evidence['listed_at']:return '尚未上市'
    if evidence.get('delisted_at') and day>=evidence['delisted_at']:return '已退市'
    for interval in evidence.get('suspensions',[]):
        if interval['start']<=day<=interval['end']:return '已核实停牌'
    return None

def ranges(days, calendar, reasons=None, positions=None):
    if not days:return []
    order=positions if positions is not None else {d:i for i,d in enumerate(calendar)};out=[]
    for day in sorted(set(days)):
        reason=(reasons or {}).get(day,'待核验')
        if out and order.get(day,-2)==order.get(out[-1]['end'],-4)+1 and out[-1]['reason']==reason:
            out[-1]['end']=day;out[-1]['count']+=1
        else:out.append(dict(start=day,end=day,count=1,reason=reason))
    return out

def gaps(present, calendar, evidence=None, causes=None):
    present=set(present)
    if not present:return dict(missing=[],normal=[],ranges=[])
    first,last=min(present),max(present)
    days=[d for d in calendar if first<=d<=last and d not in present]
    normal=[d for d in days if normal_reason(d,evidence)]
    reasons={d:normal_reason(d,evidence) or (causes or {}).get(d,'待核验') for d in days}
    return dict(missing=[d for d in days if d not in set(normal)],normal=normal,ranges=ranges(days,calendar,reasons))

def elapsed(stamp, at):
    if not stamp:return None
    try:return max(0,(at-datetime.fromisoformat(stamp)).total_seconds())
    except (ValueError,TypeError):return None

def assess(index, state, calendar, at, config=None, evidence=None):
    from .daily_sync import freshness
    p=policy(config);target=freshness(None,calendar,at)['expected_date'];evidence=evidence or {}
    positions={d:i for i,d in enumerate(calendar)}
    rows=[];jobs=state.get('jobs',{});target_matches=target==state.get('target')
    for symbol,item in index.items():
        job=jobs.get(symbol,{}) if target_matches else {}
        ev=evidence.get(symbol,{})
        excluded=normal_reason(target,ev) if target else None
        last=max(filter(None,[item.get('last_date'),job.get('last_date')]),default=None)
        lag=sum(not normal_reason(d,ev) for d in calendar[bisect_right(calendar,last):bisect_right(calendar,target)]) if target and last else None
        missing=[d for d in item.get('missing_dates',[]) if not normal_reason(d,ev)]
        normal=[d for d in item.get('missing_dates',[]) if normal_reason(d,ev)]
        failed=job.get('status') in ('retry','exhausted') and not excluded
        # Unverified source absence is separate from an exception; never call it suspension.
        category=job.get('error_category') or ('incomplete' if job.get('error')=='source_incomplete_or_suspended' else 'unknown')
        rows.append(dict(item,symbol=symbol,last_date=last,lag_sessions=lag,excluded=excluded,
            failed=failed,status='skipped' if excluded else job.get('status','pending'),
            error_category=category if failed else None,error_message=job.get('error_message') or job.get('error'),
            last_failure=job.get('last_failure') or item.get('last_failure'),
            consecutive_failures=job.get('consecutive_failures',item.get('consecutive_failures',0)),
            last_success=job.get('last_success') or item.get('last_success'),
            gap_count=len(missing),normal_gap_count=len(normal),
            gap_ranges=ranges(missing+normal,calendar,{d:normal_reason(d,ev) or item.get('gap_causes',{}).get(d,'待核验') for d in missing+normal},positions),
            missing_dates=missing, evidence=ev or None))
    expected=[r for r in rows if not r['excluded']];success=sum(r['status']=='current' for r in expected)
    completion=success/len(expected) if expected else None
    counts=dict(total=len(rows),expected=len(expected),success=success,failed=sum(r['failed'] for r in rows),
        skipped=len(rows)-len(expected),stale=sum(r['lag_sessions'] is not None and r['lag_sessions']>=p['stale_warn_sessions'] and not r['excluded'] for r in rows),
        missing=sum(not r['last_date'] and not r['excluded'] for r in rows),gaps=sum(r['gap_count']>0 for r in rows),
        normal_gaps=sum(r['normal_gap_count']>0 for r in rows),unverified=sum(not r['evidence'] for r in rows),unaudited=sum(not r.get('audit_at') or r.get('audit_source')=='catalog_only' for r in rows))
    rules=[]
    def add(key,level,message,view):rules.append(dict(key=key,level=level,message=message,view=view))
    if not target:add('calendar_unknown','error','交易日历未覆盖当前日期，无法判断应更新日','calendar')
    remaining=(date.fromisoformat(calendar[-1])-at.date()).days if calendar else None
    if remaining is not None and remaining<p['calendar_warn_days']:add('calendar_expiry','attention',f'日历仅剩 {remaining} 天覆盖','calendar')
    if counts['stale']:add('stale','error' if any((r['lag_sessions'] or 0)>=p['stale_error_sessions'] and not r['excluded'] for r in rows) else 'attention',f"{counts['stale']} 只证券达到过期阈值",'stale')
    if counts['failed']:add('failures','attention',f"{counts['failed']} 只证券采集失败或返回不完整",'failed')
    if counts['gaps']:add('gaps','attention',f"{counts['gaps']} 只证券有尚未解释的历史缺口",'gaps')
    if counts['unaudited']:add('unaudited','attention',f"{counts['unaudited']} 只证券尚未完成历史覆盖审计",'unaudited')
    if counts['unverified']:add('evidence','attention',f"{counts['unverified']} 只证券暂无可用的上市/停牌状态证据",'unverified')
    if counts['missing']:add('missing','attention',f"{counts['missing']} 只证券没有日线",'missing')
    if target:
        deadline=datetime.fromisoformat(target+'T17:30:00').replace(tzinfo=at.tzinfo)
        age=(at-deadline).total_seconds()
        if not target_matches and age>p['schedule_grace_minutes']*60:add('not_started','error','应启动的目标日任务尚未启动','tasks')
        if completion is not None and completion<p['completion_min'] and age>p['completion_grace_hours']*3600:add('completion','error','超过完成宽限期，完成率低于阈值','pending')
    invocation=state.get('invocation',{})
    idle=elapsed(invocation.get('heartbeat'),at);duration=elapsed(invocation.get('started_at'),at)
    if invocation.get('status')=='running' and ((idle is not None and idle>p['heartbeat_minutes']*60) or (duration is not None and duration>p['invocation_max_seconds'])):
        add('stalled','error',f'采集任务停滞，距最后进度 {round((idle or 0)/60,1)} 分钟','tasks')
    bad_calendar=[d for d in calendar if date.fromisoformat(d).weekday()>4 or any(a<=d<=b for a,b in HOLIDAYS_2026)]
    if bad_calendar:add('calendar_weekend','error','交易日历包含周末或交易所公布的2026年休市日','calendar')
    stale_buckets=[dict(label=f'1–{p["stale_warn_sessions"]-1} 个交易日',minimum=1,maximum=p['stale_warn_sessions']-1),dict(label=f'{p["stale_warn_sessions"]}–{p["stale_error_sessions"]-1} 个交易日',minimum=p['stale_warn_sessions'],maximum=p['stale_error_sessions']-1),dict(label=f'≥ {p["stale_error_sessions"]} 个交易日',minimum=p['stale_error_sessions'],maximum=None)]
    for bucket in stale_buckets:bucket['count']=sum(not r['excluded'] and r['lag_sessions'] is not None and r['lag_sessions']>=bucket['minimum'] and (bucket['maximum'] is None or r['lag_sessions']<=bucket['maximum']) for r in rows)
    return dict(stale_buckets=[b for b in stale_buckets if b['maximum'] is None or b['maximum']>=b['minimum']],generated_at=at.isoformat(),target_date=target,counts=counts,completion=completion,
        state='error' if any(r['level']=='error' for r in rules) else 'attention' if rules else 'normal',
        rules=rules,thresholds=p,rows=rows,calendar=dict(start=calendar[0] if calendar else None,end=calendar[-1] if calendar else None,remaining_days=remaining,invalid_days=bad_calendar),
        run=dict(started_at=state.get('started_at'),finished_at=state.get('finished_at'),invocation=invocation))

def select(rows, view='all', query='', reason='', sort='failures', stale_threshold=2):
    predicates={'all':lambda r:True,'expected':lambda r:not r['excluded'],'success':lambda r:r['status']=='current' and not r['excluded'],
        'failed':lambda r:r['failed'],'skipped':lambda r:bool(r['excluded']),'stale':lambda r:not r['excluded'] and (r['lag_sessions'] or 0)>=stale_threshold,
        'missing':lambda r:not r['last_date'] and not r['excluded'],'gaps':lambda r:r['gap_count']>0,'normal_gaps':lambda r:r['normal_gap_count']>0,
        'pending':lambda r:not r['excluded'] and r['status']!='current','unverified':lambda r:not r['evidence'],'unaudited':lambda r:not r.get('audit_at') or r.get('audit_source')=='catalog_only'}
    if view not in predicates:raise ValueError('未知明细类型')
    out=[r for r in rows if predicates[view](r) and (not query or query.lower() in (r['symbol']+r.get('name','')).lower()) and (not reason or r['error_category']==reason)]
    key={'failures':'consecutive_failures','gaps':'gap_count','stale':'lag_sessions'}.get(sort,'consecutive_failures')
    return sorted(out,key=lambda r:(-(r.get(key) or 0),r['symbol']))
