"""Account-scoped workspace, optimistic append-only revisions, transition reminders."""
import copy, hashlib, threading
from collections import defaultdict
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,ConfigDict
from . import core,comparison,research_cards as cards,daily_sync,security_research

LOCKS=defaultdict(threading.Lock)
DEFAULT_GROUPS=[dict(id='focus',name='重点研究',symbols=['002463.SZ','002384.SZ','603256.SH','002938.SZ','002484.SZ','600031.SH'])]

def prefix(user):return 'users/'+user+'/research-workspace/'
def state(store,user):
    keys=store.list_keys(prefix(user)+'revisions/')
    if not keys:return dict(revision=0,groups=copy.deepcopy(DEFAULT_GROUPS),settings=dict(range=120,show_ma=True,reminders=True),baselines={},alerts=[])
    return store.get_json(max(keys))

def commit(store,user,previous,value):
    revision=previous['revision']+1;value=dict(value,revision=revision,updated_at=core.iso())
    if not store.create_json(prefix(user)+f'revisions/{revision:012d}.json.gz',value):raise HTTPException(409,'工作区已在其他页面更新，请重新读取后再保存')
    return value

class Group(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$');name:str=Field(min_length=1,max_length=32)
    symbols:list[str]=Field(default_factory=list,max_length=100)
class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    range:int=120;show_ma:bool=True;reminders:bool=True
class Update(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0);groups:list[Group]=Field(max_length=20);settings:Settings
class Evaluate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbols:list[str]=Field(min_length=1,max_length=4)
class Ack(BaseModel):
    ids:list[str]=Field(max_length=100)

def key(symbol,end):return 'system/research-cards/'+cards.VERSION+'/'+end+'/'+symbol+'.json.gz'

class CompletePriceCache:
    """Retry incomplete end-of-day fetches; do not freeze an upstream delay all day."""
    def __init__(self,store,target):self.store=store;self.target=target
    def get_json(self,key,optional=False):
        value=self.store.get_json(key,optional=optional)
        if key.startswith('system/comparison-prices/') and value:
            rows=value.get('rows',[])
            if not rows or rows[-1][0]!=self.target:return None
        return value
    def put_json(self,key,value):return self.store.put_json(key,value)

def observe(store,user,results):
    for attempt in range(3):
        previous=state(store,user);updated=copy.deepcopy(previous)
        for card in results:
            symbol=card['symbol'];baseline,changes=cards.transition(previous['baselines'].get(symbol),card)
            updated['baselines'][symbol]=baseline
            if not previous['settings']['reminders']:continue
            for change in changes:
                identity=dict(symbol=symbol,date=card['expected_date'],**change,previous_revision=previous['revision'])
                updated['alerts'].append(dict(id=hashlib.sha256(core.dumps(identity).encode()).hexdigest()[:24],symbol=symbol,name=card['name'],at=core.iso(),asof=card['asof'],expected_date=card['expected_date'],read=False,data_hash=card['data_hash'],**change))
        updated['alerts']=updated['alerts'][-100:]
        try:return commit(store,user,previous,updated)
        except HTTPException:
            if attempt==2:raise

def routes(store_factory,user_for,check_action,page=None):
    router=APIRouter()
    @router.get('/research')
    def research_page():return page() if page else FileResponse(core.ROOT/'app/static/research-workspace/index.html')
    @router.get('/api/research-workspace')
    def get_state(request:Request):return state(store_factory(),user_for(request))
    @router.put('/api/research-workspace')
    def update(body:Update,request:Request):
        check_action(request);store=store_factory();user=user_for(request);previous=state(store,user)
        if body.revision!=previous['revision']:raise HTTPException(409,'工作区版本已改变，请重新读取')
        if body.settings.range not in (60,120,250,2000):raise HTTPException(400,'不支持的图表范围')
        if len({g.id for g in body.groups})!=len(body.groups):raise HTTPException(400,'分组ID重复')
        if any(not g.name.strip() for g in body.groups):raise HTTPException(400,'分组名称不能为空')
        known=core.universe();symbols={s for g in body.groups for s in g.symbols}
        if len(symbols)>200 or any(s not in known for s in symbols):raise HTTPException(400,'证券不存在或自选总量超过200只')
        value=dict(previous,groups=[dict(id=g.id,name=g.name.strip(),symbols=list(dict.fromkeys(g.symbols))) for g in body.groups],settings=body.settings.model_dump())
        return commit(store,user,previous,value)
    @router.post('/api/research-workspace/evaluate')
    def evaluate(body:Evaluate,request:Request):
        check_action(request);user=user_for(request);store=store_factory();symbols=list(dict.fromkeys(security_research.resolve(s)['symbol'] for s in body.symbols))
        lock=LOCKS[user]
        if not lock.acquire(False):raise HTTPException(409,'本账号已有研究检查正在运行')
        try:
            target=daily_sync.freshness(None,core.calendar_dates())['expected_date']
            if not target:raise HTTPException(422,'交易日历不足，停止研究判断')
            results=[];errors=[]
            for symbol in symbols:
                try:
                    result=store.get_json(key(symbol,target),optional=True)
                    if not result or not result['usable']:
                        snapshot=comparison.acquire(list(dict.fromkeys([symbol,cards.BENCHMARK])),CompletePriceCache(store,target))
                        result=cards.build(snapshot,symbol)
                        if all(a['rows'] and a['rows'][-1][0]==target for a in snapshot['assets'].values()) and result['usable']:
                            store.put_json(key(symbol,target),result)
                    results.append(result)
                except Exception as exc:
                    # Failed acquisition is not a neutral indicator state. Preserve diagnosis without secrets.
                    from .data_health import error_info
                    info=error_info(exc,(getattr(store,'key',''),));errors.append(dict(symbol=symbol,**info))
                    results.append(dict(symbol=symbol,name=(core.instrument(symbol) or {}).get('name',symbol),usable=False,asof=None,expected_date=target,version=cards.VERSION,data_hash=None,items=[],states={k:'unknown' for k in ('trend','momentum','volatility','activity','relative')},conflicts=[],chart=[],quality={},notes=['数据源或校验失败，暂不判断'],error=info))
            current=observe(store,user,results)
            return dict(cards=results,errors=errors,workspace=current,notice='日更研究；仅在执行检查时生成站内变化提醒，不进行后台推送。')
        finally:lock.release()
    @router.post('/api/research-workspace/ack')
    def acknowledge(body:Ack,request:Request):
        check_action(request);store=store_factory();user=user_for(request)
        for attempt in range(3):
            previous=state(store,user);updated=copy.deepcopy(previous)
            for alert in updated['alerts']:
                if alert['id'] in body.ids:alert['read']=True
            try:return commit(store,user,previous,updated)
            except HTTPException:
                if attempt==2:raise
    return router
