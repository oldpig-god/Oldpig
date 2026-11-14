"""Daily sector discovery. Standard-library worker; HTTP dependencies load in routes()."""
import hashlib,math,statistics,time
from itertools import combinations
from . import core,industry_ths

PREFIX='system/industry-discovery/'
STATE=PREFIX+'state.json.gz'
LATEST=PREFIX+'latest.json.gz'
WINDOWS=(60,120,250)
MIN_SAMPLES=30

def validate(rows,calendar):
    allowed=set(calendar);seen=set()
    for row in rows:
        d,o,c,h,l,v=row[:6]
        if d not in allowed or d in seen or not all(math.isfinite(x) for x in (o,c,h,l,v)) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('行业行情校验失败')
        seen.add(d)

def returns(rows,calendar):
    prices={r[0]:r[2] for r in rows if r[5]>0}
    return [prices[b]/prices[a]-1 if a in prices and b in prices else None for a,b in zip(calendar,calendar[1:])]

def stats(a,b):
    paired=[(x,y) for x,y in zip(a,b) if x is not None and y is not None];n=len(paired)
    if n<MIN_SAMPLES:return dict(n=n,correlation=None,covariance=None,volatility_a=None,volatility_b=None)
    x,y=zip(*paired);mx=sum(x)/n;my=sum(y)/n
    vx=sum((v-mx)**2 for v in x)/(n-1);vy=sum((v-my)**2 for v in y)/(n-1);cov=sum((v-mx)*(w-my) for v,w in paired)/(n-1)
    corr=cov/math.sqrt(vx*vy) if vx>1e-24 and vy>1e-24 else None
    return dict(n=n,correlation=max(-1,min(1,corr)) if corr is not None else None,covariance=cov,volatility_a=math.sqrt(vx*252),volatility_b=math.sqrt(vy*252))

def build(profiles,calendar,target,total,failures=None):
    dates=[d for d in calendar if d<=target][-311:];series={k:returns(v['rows'],dates) for k,v in profiles.items()};pairs=[]
    for a,b in combinations(sorted(profiles),2):
        windows={str(w):stats(series[a][-w:],series[b][-w:]) for w in WINDOWS}
        formation={str(w):stats(series[a][-w-60:-60],series[b][-w-60:-60]) for w in WINDOWS}
        current=[x['correlation'] for x in windows.values()];last=min(profiles[a]['last_date'],profiles[b]['last_date'])
        pairs.append(dict(id=a+'_'+b,left=a,right=b,left_name=profiles[a]['name'],right_name=profiles[b]['name'],windows=windows,formation=formation,validation=windows['60'],consistent_negative=all(c is not None and c<0 for c in current),last_date=last,stale=last<target))
    items=[dict(code=k,name=v['name'],last_date=v['last_date']) for k,v in sorted(profiles.items())]
    return dict(version=1,target=target,generated_at=core.iso(),total_industries=total,covered_industries=len(items),current_industries=sum(i['last_date']==target for i in items),possible_pairs=total*(total-1)//2,computed_pairs=len(pairs),items=items,pairs=pairs,failures=failures or [],formation_end=dates[-61] if len(dates)>60 else None,validation_start=dates[-60] if len(dates)>=60 else None,calendar_start=dates[0] if dates else None,source=industry_ths.SOURCE)

def due(store,target,stamp):
    state=store.get_json(STATE,optional=True)
    return not state or state['target']!=target or (not state.get('complete') and state.get('retry_at',0)<=stamp)

def sync_tick(store,target,calendar,budget=100,clock=time.monotonic):
    from concurrent.futures import ThreadPoolExecutor
    start_tick=clock();state=store.get_json(STATE,optional=True)
    if not state or state['target']!=target:
        listing=industry_ths.fetch_catalog();store.put_json(industry_ths.PREFIX+'catalog.json.gz',listing)
        carry=(state or {}).get('profiles',{})
        state=dict(target=target,items=listing['items'],jobs={},profiles={},carry_profiles=carry,started_at=core.iso(),complete=False)
        store.put_json(STATE,state)
    if state['complete']:return dict(complete=True,processed=0,total=len(state['items']))
    year_start=int(core.history_start()[:4]);year_end=int(target[:4]);processed=0;now=core.now().timestamp()
    jobs=state['jobs']
    for item in state['items']:jobs.setdefault(item['code'],dict(year=year_end if item['code'] in state.get('carry_profiles',{}) else year_start,attempts=0,status='pending'))
    def collect(item):
        code=item['code'];year=jobs[code]['year'];work_key=PREFIX+'work/'+target+'/'+code+'.json.gz'
        try:
            annual=industry_ths.load_year(store,code,year,target,calendar,validate)
            work=store.get_json(work_key,optional=True) or {'rows':state.get('carry_profiles',{}).get(code,{}).get('rows',[])};rows={r[0]:r for r in work['rows']}
            rows.update({r[0]:r for r in annual['rows'] if core.history_start()<=r[0]<=target})
            work={'rows':[rows[d] for d in sorted(rows)]};store.put_json(work_key,work)
            if year==year_end:
                if not work['rows']:return dict(error='EmptyHistory')
                profile=dict(name=item['name'],last_date=work['rows'][-1][0],rows=work['rows'][-311:])
                return dict(profile=profile,error='SourceNotUpdated' if profile['last_date']<target else None)
            return {}
        except Exception as exc:return dict(error=type(exc).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:
        while clock()-start_tick<budget:
            due_items=[i for i in state['items'] if jobs[i['code']]['status']!='current' and jobs[i['code']]['attempts']<4 and jobs[i['code']].get('retry_at',0)<=now][:2]
            if not due_items:break
            for item in due_items:jobs[item['code']].update(status='running',heartbeat=core.iso())
            state['updated_at']=core.iso();store.put_json(STATE,state)
            for item,result in zip(due_items,pool.map(collect,due_items)):
                code=item['code'];job=jobs[code]
                if result.get('profile'):state['profiles'][code]=result['profile']
                if result.get('error'):job.update(status='retry',attempts=job['attempts']+1,error=result['error'],retry_at=now+900)
                else:
                    processed+=1;job['year']+=1;job['status']='current' if job['year']>year_end else 'pending'
                    if job['status']=='current':job['last_date']=state['profiles'][code]['last_date']
            state['updated_at']=core.iso();store.put_json(STATE,state)
    finished=all(j['status']=='current' or j['attempts']>=4 for j in jobs.values())
    failures=[dict(code=i['code'],name=i['name'],status=jobs[i['code']]['status'],error=jobs[i['code']].get('error')) for i in state['items'] if jobs[i['code']]['status']!='current']
    report=build(state['profiles'],calendar,target,len(state['items']),failures)
    store.put_json(PREFIX+'snapshots/'+target+'.json.gz',report);store.put_json(LATEST,report)
    retry_at=min((j.get('retry_at',0) for j in jobs.values() if j['status']!='current' and j['attempts']<4),default=0)
    state.update(complete=finished,updated_at=core.iso(),retry_at=retry_at);store.put_json(STATE,state)
    return dict(complete=finished,processed=processed,current=sum(j['status']=='current' for j in jobs.values()),total=len(state['items']),computed_pairs=len(report['pairs']))

def pair_state(pair,window):
    if not pair:return 'missing'
    if pair['stale']:return 'stale'
    c=pair['windows'][str(window)]['correlation']
    return 'insufficient' if c is None else 'negative' if c<-.1 else 'positive' if c>.1 else 'near_zero'

LABELS=dict(missing='暂无计算结果',stale='数据日期落后',insufficient='样本不足',negative='负相关',positive='正相关',near_zero='接近零相关')

def watch_prefix(user):return 'users/'+user+'/industry-watch/'

def evaluate_watch(watch,pair,target):
    current=pair_state(pair,watch['window']);previous=watch.get('state');alerts=watch.get('alerts',[])
    if previous is not None and current!=previous:
        identity=hashlib.sha256((watch['id']+target+previous+current).encode()).hexdigest()[:20]
        if not any(a['id']==identity for a in alerts):alerts.append(dict(id=identity,date=target,text=LABELS[previous]+' → '+LABELS[current],read=False))
    if pair and current not in ('missing','stale','insufficient'):
        metric=pair['windows'][str(watch['window'])];old=watch.get('metric')
        if old and watch.get('date')!=target:
            for key,label in [('volatility_a','A波动率'),('volatility_b','B波动率')]:
                if old.get(key) and metric[key]>old[key]*1.2:
                    identity=hashlib.sha256((watch['id']+target+key).encode()).hexdigest()[:20]
                    if not any(a['id']==identity for a in alerts):alerts.append(dict(id=identity,date=target,text=label+'较上次观察上升超过20%',read=False))
        watch=dict(watch,metric=dict(metric))
    return dict(watch,state=current,date=target,alerts=alerts[-30:])

def routes(store_factory,user_for,check_action,page=None):
    from fastapi import APIRouter,HTTPException,Request,Query
    from fastapi.responses import FileResponse
    router=APIRouter()
    @router.get('/discover')
    def discover():return page() if page else FileResponse(core.ROOT/'app/static/discover/index.html')
    def snapshot(store):
        value=store.get_json(LATEST,optional=True)
        if value is None:raise HTTPException(503,'行业数据正在准备，完成首批后即可查看')
        from .daily_sync import freshness
        expected=freshness(None,core.calendar_dates())['expected_date']
        value=dict(value,expected_target=expected,snapshot_stale=expected is None or value['target']!=expected)
        if value['snapshot_stale']:
            value['pairs']=[dict(p,stale=True) for p in value['pairs']]
        return value
    @router.get('/api/discovery')
    def listing(window:int=250,search:str='',negative:bool=False,stable:bool=False,current_only:bool=True,min_samples:int=Query(30,ge=30,le=250),sort:str='formation',page_number:int=Query(1,ge=1),limit:int=Query(30,ge=1,le=100)):
        if window not in WINDOWS or sort not in ('formation','correlation','validation'):raise HTTPException(400,'窗口或排序不支持')
        data=snapshot(store_factory());rows=[];q=search.strip().lower()
        for pair in data['pairs']:
            m=pair['windows'][str(window)];train=pair['formation'][str(window)]
            if q and q not in (pair['left_name']+pair['right_name']+pair['left']+pair['right']).lower():continue
            rank_samples=train['n'] if sort=='formation' else pair['validation']['n'] if sort=='validation' else m['n']
            if current_only and pair['stale'] or min(m['n'],rank_samples)<min_samples:continue
            if negative and (m['correlation'] is None or m['correlation']>=0):continue
            if stable and not pair['consistent_negative']:continue
            rows.append(pair)
        def rank(p):
            value=p['formation'][str(window)]['correlation'] if sort=='formation' else p['validation']['correlation'] if sort=='validation' else p['windows'][str(window)]['correlation']
            return (value is None,value if value is not None else 0,p['id'])
        rows.sort(key=rank);offset=(page_number-1)*limit
        return dict(**{k:v for k,v in data.items() if k not in ('pairs',)},total=len(rows),page=page_number,limit=limit,rows=rows[offset:offset+limit],window=window)
    @router.get('/api/discovery/matrix')
    def matrix(window:int=250):
        if window not in WINDOWS:raise HTTPException(400,'窗口不支持')
        data=snapshot(store_factory())
        return dict(items=data['items'],target=data['target'],cells=[dict(left=p['left'],right=p['right'],value=p['windows'][str(window)]['correlation'],n=p['windows'][str(window)]['n'],stale=p['stale']) for p in data['pairs']])
    @router.get('/api/discovery/watch')
    def watches(request:Request):
        store=store_factory();data=snapshot(store);pairs={p['id']:p for p in data['pairs']};items=[]
        for key in store.list_keys(watch_prefix(user_for(request))):
            old=store.get_json(key)
            if not old.get('active',True):continue
            value=evaluate_watch(old,pairs.get(old['pair_id']),data['target'])
            if value!=old:store.put_json(key,value)
            items.append(dict(value,pair=pairs.get(old['pair_id'])))
        return dict(items=items,target=data['target'])
    @router.post('/api/discovery/watch')
    def save(body:dict,request:Request):
        check_action(request);store=store_factory();data=snapshot(store);pair=next((p for p in data['pairs'] if p['id']==body.get('pair_id')),None);window=body.get('window',250)
        if pair is None or window not in WINDOWS:raise HTTPException(400,'请选择有效组合和窗口')
        identity=pair['id']+'_'+str(window);key=watch_prefix(user_for(request))+identity+'.json.gz';old=store.get_json(key,optional=True)
        value=old or dict(id=identity,pair_id=pair['id'],window=window,left_name=pair['left_name'],right_name=pair['right_name'],created_at=core.iso(),alerts=[])
        value=evaluate_watch(dict(value,active=True),pair,data['target']);store.put_json(key,value);return value
    @router.post('/api/discovery/watch/{watch_id}/action')
    def watch_action(watch_id:str,body:dict,request:Request):
        import re
        check_action(request)
        if not re.fullmatch(r'THS\d{6}_THS\d{6}_(60|120|250)',watch_id):raise HTTPException(400,'观察编号无效')
        store=store_factory();key=watch_prefix(user_for(request))+watch_id+'.json.gz';value=store.get_json(key,optional=True)
        if value is None:raise HTTPException(404,'组合未保存')
        if body.get('action')=='remove':value['active']=False
        elif body.get('action')=='read':
            for alert in value['alerts']:alert['read']=True
        else:raise HTTPException(400,'操作不支持')
        store.put_json(key,value);return {'ok':True}
    return router
