"""Loopback-only FastAPI workbench; no credentials, no browser-side market scraping."""
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import core, collector, market, backfill, github_history
import subprocess
import sys
from functools import lru_cache

JOB = {'running':False,'started_at':None,'finished_at':None,'progress':0,'total':0,'last_result':None,'error':None}
LOCK=threading.Lock()
AUTO={'enabled':False,'interval_seconds':60,'last_attempt':0.,'daily_date':None,'close_date':None,'cooldown_until':0.}
STOP=threading.Event()
TECH_JOB={'running':False,'started_at':None,'finished_at':None,'error':None}
TECH_LOCK=threading.Lock()


def snapshot():
    with LOCK:return dict(JOB)


def start_job(intervals, symbols=None, completed_marker=None):
    symbols=symbols or list(core.universe())
    with LOCK:
        if JOB['running']:return False
        JOB.update(running=True,started_at=core.iso(),finished_at=None,progress=0,total=len(symbols)*len(intervals),error=None,last_result=None)
    def progress(row):
        with LOCK:JOB.update(progress=JOB['progress']+1,last_result=row)
    def run():
        try:
            result=collector.collect(symbols,intervals,progress=progress)
            failures=sum(r['status']=='error' for r in result['results'])
            with LOCK:
                if result['status']=='busy':JOB['error']='已有采集进程运行，请等待该进程完成'
                if failures:
                    JOB['error']=f'{failures}个接口失败，已有数据保留'
                    AUTO['cooldown_until']=time.monotonic()+300
                if completed_marker and result['status']=='done':
                    if all(r['status']=='ok' for r in result['results']):
                        AUTO[completed_marker[0]]=completed_marker[1]
                    else:
                        AUTO['cooldown_until']=time.monotonic()+300
        except Exception as exc:
            with LOCK:JOB['error']=str(exc)[:400]
        finally:
            with LOCK:JOB.update(running=False,finished_at=core.iso())
    threading.Thread(target=run,daemon=True).start()
    return True


def start_market_job(completed_marker=None):
    with LOCK:
        if JOB['running']:return False
        JOB.update(running=True,started_at=core.iso(),finished_at=None,progress=0,total=2,error=None,last_result=None)
    def run():
        try:
            with core.db() as c:r=c.execute("SELECT value FROM metadata WHERE key='market_inventory'").fetchone()
            today=core.now().astimezone(core.SH).date().isoformat()
            if not r or datetime.fromisoformat(core.json.loads(r['value'])['fetched_at']).astimezone(core.SH).date().isoformat()!=today:
                market.refresh(inventory=True)
            results=market.refresh()
            backfill.enqueue()
            with LOCK:
                JOB.update(progress=2,last_result=results[-1] if results else None)
                if completed_marker and all(r['status']=='ok' for r in results):AUTO[completed_marker[0]]=completed_marker[1]
                errors=[r for r in results if r['status']=='error']
                if errors:
                    JOB['error']=f'{len(errors)}类全市场快照失败，保留旧数据'
                    AUTO['cooldown_until']=time.monotonic()+300
        except Exception as exc:
            with LOCK:JOB['error']=str(exc)[:400]
        finally:
            with LOCK:JOB.update(running=False,finished_at=core.iso())
    threading.Thread(target=run,daemon=True).start()
    return True


def launch_backfill():
    log=(core.DATA/'backfill.log').open('a')
    try:
        process=subprocess.Popen([sys.executable,'-m','app.backfill'],cwd=core.ROOT,stdout=log,stderr=log,start_new_session=True)
        return process.pid
    finally:log.close()


def scheduler():
    while not STOP.wait(5):
        if not AUTO['enabled'] or snapshot()['running']:continue
        stamp=time.monotonic()
        if stamp<AUTO['cooldown_until'] or stamp-AUTO['last_attempt']<AUTO['interval_seconds']:continue
        state=core.calendar_state();local=core.now().astimezone(core.SH)
        if state['state']=='trading':
            AUTO['last_attempt']=stamp;start_market_job()
        elif state['date'] in core.calendar_dates() and local.hour*60+local.minute>=901 and AUTO['close_date']!=state['date']:
            AUTO['last_attempt']=stamp
            start_market_job(('close_date',state['date']))
        if state['date'] in core.calendar_dates() and local.hour*60+local.minute>=1050:
            with core.db() as c:
                r=c.execute("SELECT value FROM metadata WHERE key='daily_enqueued_date'").fetchone()
                if not r or r['value']!=state['date']:
                    c.execute("UPDATE history_tasks SET status='pending' WHERE interval='1d' AND status!='running'")
                    c.execute("INSERT OR REPLACE INTO metadata VALUES('daily_enqueued_date',?)",(state['date'],))
                    launch_backfill()


@asynccontextmanager
async def lifespan(app):
    core.init();STOP.clear();AUTO['enabled']=os.environ.get('FINANCE_AUTO_REFRESH')=='1'
    worker=threading.Thread(target=scheduler,daemon=True);worker.start()
    yield
    STOP.set()


app=FastAPI(title='观澜本地金融工作台',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=core.ROOT/'app/static'),name='static')


@app.get('/technical')
def technical_page():return FileResponse(core.ROOT/'app/static/technical/index.html')


@app.get('/api/technical')
def technical_analysis():
    path=core.ROOT/'app/static/technical/analysis.json'
    if not path.exists():raise HTTPException(404,'尚无技术研究结果，请先运行 python -m app.technical')
    return FileResponse(path,media_type='application/json',headers={'Cache-Control':'no-store'})


@app.get('/api/technical/status')
def technical_status():
    with TECH_LOCK:return dict(TECH_JOB)


class TechnicalSelection(BaseModel):
    symbols:list[str]=Field(min_length=1,max_length=12)


@app.get('/api/technical/search')
def technical_search(q:str=''):
    with core.db() as c:
        rows=c.execute("SELECT symbol,name,kind FROM instruments WHERE active=1 AND (instr(name,?)>0 OR instr(symbol,?)>0) ORDER BY symbol LIMIT 20",(q.strip(),q.strip().upper())).fetchall()
    return {'items':[dict(r) for r in rows]}


@app.post('/api/technical/refresh')
def refresh_technical(request:Request,body:TechnicalSelection|None=None):
    require_local_action(request)
    from . import technical
    try:watchlist=technical.resolve_watchlist(body.symbols) if body else None
    except ValueError as exc:raise HTTPException(400,str(exc))
    with TECH_LOCK:
        if TECH_JOB['running']:raise HTTPException(409,'技术研究正在更新')
        TECH_JOB.update(running=True,started_at=core.iso(),finished_at=None,error=None)
    def run():
        try:
            from . import technical
            if watchlist is None:technical.build()
            else:technical.build(watchlist=watchlist)
        except Exception as exc:
            with TECH_LOCK:TECH_JOB['error']=str(exc)[:500]
        finally:
            with TECH_LOCK:TECH_JOB.update(running=False,finished_at=core.iso())
    threading.Thread(target=run,daemon=True).start()
    return technical_status()


@app.get('/')
def index():return FileResponse(core.ROOT/'app/static/index.html')


@app.get('/api/health')
def health():
    cov=coverage()
    counts={r['interval']:r['rows'] for r in cov['history']}
    with core.db() as c:batches=c.execute('SELECT count(*) FROM batches').fetchone()[0]
    return dict(mode='source_data',server_time=core.iso(),market=core.calendar_state(),counts=counts,batches=batches,
                auto_refresh={k:v for k,v in AUTO.items() if k not in ('last_attempt','cooldown_until')},job=snapshot())


def asset_snapshot(symbol):
    daily=core.observations(symbol,'1d',2);minute=core.observations(symbol,'1m',1)
    last=minute[-1] if minute and (not daily or minute[-1]['event_time']>=daily[-1]['event_time']) else daily[-1] if daily else None
    interval=last['interval'] if last else '1d'
    # Preserve upstream daily pct rather than computing an unadjusted corporate-action return.
    return dict(core.instrument(symbol),latest=last,daily=daily[-1] if daily else None,
                freshness=core.freshness(last,interval),daily_pct_source=daily[-1].get('pct_change_source') if daily else None)


@app.get('/api/universe')
def universe(q:str='',kind:str='all',exchange:str='all',offset:int=0,limit:int=50,sort:str='code'):
    if kind not in ('all','stock','etf') or exchange not in ('all','SH','SZ','BJ'):raise HTTPException(400,'筛选条件不支持')
    offset=max(0,offset);limit=max(1,min(100,limit));where=['i.active=1'];args=[]
    if kind!='all':where.append('i.kind=?');args.append(kind)
    if exchange!='all':where.append('i.exchange=?');args.append(exchange)
    if q:
        where.append('(instr(i.name,?)>0 OR instr(i.symbol,?)>0)');args.extend([q.strip(),q.strip().upper()])
    clause=' AND '.join(where);order='i.name,i.symbol' if sort=='name' else 'i.symbol'
    with core.db() as c:
        total=c.execute('SELECT count(*) FROM instruments i WHERE '+clause,args).fetchone()[0]
        all_count=c.execute('SELECT count(*) FROM instruments WHERE active=1').fetchone()[0]
        rows=c.execute('SELECT i.*,q.payload quote,q.batch_id quote_batch,t.status history_status FROM instruments i LEFT JOIN market_quotes q USING(symbol) LEFT JOIN history_tasks t ON t.symbol=i.symbol AND t.interval="1d" WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    items=[]
    for row in rows:
        item=dict(row);raw=item.pop('quote');quote=core.json.loads(raw) if raw else None
        if quote:
            quote['batch_id']=item.pop('quote_batch');item['latest']=quote
            age=(core.now()-datetime.fromisoformat(quote['fetched_at'])).total_seconds()
            label='快照抓取已超3分钟' if age>180 else '源行情时点未知' if not quote['event_time'] else '行情快照'
            if quote['close'] is None:label='源未提供有效价格'
            item['freshness']={'state':'snapshot','label':label}
        else:
            item.pop('quote_batch',None);item['latest']=None;item['freshness']={'state':'missing','label':'快照未覆盖'}
        item['github_available']=item['symbol'] in github_history.catalog()
        items.append(item)
    return dict(items=items,total=total,universe_total=all_count,offset=offset,limit=limit,server_time=core.iso())


@lru_cache(maxsize=2)
def cached_coverage(data_dir,bucket):return market.coverage()


@app.get('/api/coverage')
def coverage():
    return dict(cached_coverage(str(core.DATA),int(time.monotonic()//15)),github_history=github_history.summary())


@app.post('/api/market-refresh')
def market_refresh(request:Request):
    require_local_action(request)
    if not start_market_job():raise HTTPException(409,'采集任务正在执行')
    return snapshot()


@app.post('/api/backfill')
def history_backfill(request:Request):
    require_local_action(request);backfill.enqueue()
    return {'status':'queued','pid':launch_backfill()}


@app.get('/api/series/{symbol}')
def series(symbol:str,interval:str='1d',limit:int=2000,source:str='primary'):
    if not core.instrument(symbol):raise HTTPException(404,'证券不在当前股票池中')
    if interval not in ('1d','1m','5m'):raise HTTPException(400,'不支持的周期')
    if source not in ('primary','github'):raise HTTPException(400,'数据源不支持')
    if source=='github':
        if interval!='1d':raise HTTPException(400,'GitHub归档仅提供日线')
        try:result=github_history.series(symbol,max(1,min(limit,10000)))
        except Exception as exc:raise HTTPException(502,'GitHub历史读取失败：'+str(exc)[:200])
        if result is None:raise HTTPException(404,'该证券不在固定版本GitHub归档中，请切换主行情')
        with core.db() as c:q=c.execute('SELECT payload FROM market_quotes WHERE symbol=?',(symbol,)).fetchone()
        return dict(result,quote=core.json.loads(q['payload']) if q else None,github_available=True)
    rows=core.observations(symbol,interval,max(1,min(limit,10000)))
    with core.db() as c:
        quote=c.execute('SELECT payload FROM market_quotes WHERE symbol=?',(symbol,)).fetchone()
        span=c.execute('SELECT min(event_time) start,max(event_time) end,count(*) count FROM observations WHERE symbol=? AND interval=? AND event_time>=?',(symbol,interval,core.history_start())).fetchone()
        batch=c.execute('SELECT * FROM batches WHERE symbol=? AND interval=? ORDER BY started_at DESC LIMIT 1',(symbol,interval)).fetchone()
    return dict(instrument=core.instrument(symbol),interval=interval,chart_type='price_line' if interval=='1m' else 'candlestick',
                history_window=core.history_window(),records=rows,quote=core.json.loads(quote['payload']) if quote else None,range=dict(span),freshness=core.freshness(rows[-1] if rows else None,interval),
                source='东方财富 / AKShare',github_available=symbol in github_history.catalog(),adjustment='不复权',volume_unit='未核验，仅保留原始量',
                last_batch=dict(batch) if batch else None,limitations=['未核验成交量单位，不用于跨源量额比较','当前数据未提供历史复权因子，不计算投资收益率'])


@app.get('/api/quality')
def quality(symbol:str|None=None):
    with core.db() as c:
        latest=c.execute('''SELECT * FROM (SELECT *,row_number() OVER(PARTITION BY symbol,interval ORDER BY started_at DESC) rn FROM batches WHERE (? IS NULL OR symbol=?)) WHERE rn=1 ORDER BY started_at DESC LIMIT 100''',(symbol,symbol)).fetchall()
        recent=c.execute('SELECT id,symbol,interval,status,started_at,finished_at,rows_in,rows_accepted,rows_rejected,changed,error,issues FROM batches ORDER BY started_at DESC LIMIT 50').fetchall()
        revisions=c.execute('SELECT count(*) n FROM revisions').fetchone()['n']
    def clean(r):
        d=dict(r);d['issues']=core.json.loads(d.get('issues') or '{}');d.pop('rn',None);return d
    return dict(latest=[clean(r) for r in latest],recent=[clean(r) for r in recent],revision_count=revisions,job=snapshot())


@app.get('/api/batches/{batch_id}')
def batch_detail(batch_id:str):
    with core.db() as c:r=c.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
    if not r:raise HTTPException(404,'批次不存在')
    result=dict(r)
    if r['raw_path']:
        data=core.read_raw(core.DATA/r['raw_path'])
        examples=[]
        for row in data['rows'] if r['interval'] in ('1d','1m','5m') else []:
            _,issues=core.normalize(row,r['symbol'],r['interval'],r['finished_at'])
            if issues:examples.append(dict(raw=row,issues=issues))
            if len(examples)>=5:break
        result['issue_examples']=examples
        result['raw_sample']=data['rows'][:3]
    return result


class Refresh(BaseModel):
    intervals:list[str]=Field(default_factory=lambda:['1m'])
    symbols:list[str]|None=None


def require_local_action(request):
    if request.headers.get('x-workbench-action')!='local-refresh':raise HTTPException(403,'缺少本地操作标记')
    origin=request.headers.get('origin')
    if origin and origin!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'仅允许本机工作台操作')


@app.post('/api/refresh')
def refresh(body:Refresh,request:Request):
    require_local_action(request)
    if not body.intervals or any(i not in ('1d','1m','5m') for i in body.intervals):raise HTTPException(400,'周期不支持')
    if body.symbols is not None and (not body.symbols or any(not core.instrument(s) for s in body.symbols)):raise HTTPException(400,'证券不支持')
    if not start_job(list(dict.fromkeys(body.intervals)),body.symbols):raise HTTPException(409,'采集任务正在执行')
    return snapshot()


class AutoRefresh(BaseModel):enabled:bool


@app.post('/api/auto-refresh')
def auto_refresh(body:AutoRefresh,request:Request):
    require_local_action(request)
    AUTO['enabled']=body.enabled
    return dict(enabled=AUTO['enabled'],interval_seconds=60,market=core.calendar_state())

# Strategy validation has a separate immutable experiment ledger.
from . import strategy_service
app.include_router(strategy_service.routes(strategy_service.LocalStore,lambda request:'local',require_local_action))

from . import security_research
app.include_router(security_research.routes(lambda symbol:series(symbol,'1d',2000,'primary')))
