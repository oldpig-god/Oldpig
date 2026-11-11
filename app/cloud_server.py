"""Request-driven CloudBase deployment of the workbench with private durable storage."""
import base64
from collections import OrderedDict,defaultdict
from contextlib import asynccontextmanager
import gzip
import hashlib
import hmac
import html
import io
import json
import os
from pathlib import Path
import secrets
import traceback
import re
import shutil
import tempfile
import threading
import time
import zipfile

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse,Response,FileResponse
import pandas as pd
import requests
from . import core,server,technical,indicators,github_history
from .cloud_store import CloudStore
from . import tencent_source

STORE=CloudStore()
CACHE=OrderedDict();CACHE_LOCK=threading.Lock();CACHE_BYTES=0
QUOTE_VERSION=None
CURRENT={};CATALOG={};COVERAGE={};LOCKS=defaultdict(threading.Lock)
COOKIE='oldpig_session';SESSION_SECONDS=7*86400
ACCOUNTS=json.loads(base64.b64decode(os.environ['FINANCE_CLOUD_USERS_B64'])) if os.environ.get('FINANCE_CLOUD_USERS_B64') else {}
SIGNING_KEY=os.environ.get('FINANCE_SESSION_KEY','')
LOGIN_FAILURES=defaultdict(list)


def record_failure(operation,exc):
    reference=secrets.token_hex(8)
    message=re.sub(r'https?://\S+','[URL omitted]',str(exc))
    for secret in [getattr(STORE,'key',''),SIGNING_KEY]:
        if secret:message=message.replace(secret,'[redacted]')
    payload={'operation':operation,'category':type(exc).__name__,'message':message[:500],
             'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)], 'at':core.iso()}
    try:STORE.put_json('diagnostics/'+reference+'.json.gz',payload)
    except Exception:pass
    return reference


def cache_get(key,ttl=300):
    with CACHE_LOCK:
        item=CACHE.get(key)
        if item and time.monotonic()-item[0]<ttl:
            CACHE.move_to_end(key);return item[1]
    value=STORE.get_json(key,optional=True)
    with CACHE_LOCK:
        CACHE[key]=(time.monotonic(),value)
        while len(CACHE)>8:CACHE.popitem(last=False)
    return value


def token_for(user):
    body=base64.urlsafe_b64encode(core.dumps({'user':user,'expires':int(time.time())+SESSION_SECONDS}).encode()).decode().rstrip('=')
    signature=hmac.new(SIGNING_KEY.encode(),body.encode(),hashlib.sha256).hexdigest()
    return body+'.'+signature


def session_user(token):
    try:
        body,signature=token.split('.')
        expected=hmac.new(SIGNING_KEY.encode(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):return None
        value=json.loads(base64.urlsafe_b64decode(body+'='*((-len(body))%4)))
        if value['expires']<time.time() or value['user'] not in ACCOUNTS:return None
        return value['user']
    except (ValueError,KeyError,TypeError):return None


def authenticated(request):return session_user(request.cookies.get(COOKIE,''))


def check_action(request):
    if request.headers.get('x-workbench-action')!='local-refresh':raise HTTPException(403,'缺少操作标记')
    origin=request.headers.get('origin')
    allowed=os.environ.get('FINANCE_PUBLIC_ORIGIN','').rstrip('/')
    if origin and origin.rstrip('/') not in (allowed,str(request.base_url).rstrip('/')):raise HTTPException(403,'仅允许同源操作')


@asynccontextmanager
async def lifespan(app):
    if not ACCOUNTS or not SIGNING_KEY:raise RuntimeError('Cloud login configuration missing')
    CURRENT.update(STORE.get_json('system/current.json.gz'))
    core.DATA.mkdir(parents=True,exist_ok=True)
    seed=STORE.get(CURRENT['seed'])
    (core.DATA/'workbench.sqlite').write_bytes(gzip.decompress(seed))
    core.init()
    CATALOG.update(STORE.get_json(CURRENT['catalog']))
    COVERAGE.update(STORE.get_json(CURRENT['coverage']))
    server.AUTO['enabled']=False
    yield


app=FastAPI(title='观澜 · 云端金融研究',lifespan=lifespan)


@app.middleware('http')
async def login_gate(request,call_next):
    path=request.url.path
    if path not in ('/login','/healthz'):
        user=authenticated(request)
        if not user:
            if path.startswith('/api/') or path.startswith('/static/'):
                return JSONResponse({'detail':'登录已过期，请重新登录','login':'/login'},status_code=401,headers={'Cache-Control':'no-store'})
            return RedirectResponse('/login',status_code=303)
        request.state.user=user
    try:response=await call_next(request)
    except Exception:
        # Cloud gateway headers and storage signed URLs must never reach a response.
        return JSONResponse({'detail':'云端暂时不可用，请稍后重试；原结果已保留'},status_code=503,headers={'Cache-Control':'no-store'})
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    return response


@app.get('/healthz')
def healthz():return {'ok':True}


LOGIN_HTML='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>观澜 · 登录</title><style>*{box-sizing:border-box}body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#edf2f7;color:#20374d;min-height:100vh;display:grid;place-items:center;padding:24px}.card{width:100%;max-width:420px;background:#fff;padding:38px;border:1px solid #dce5ee;border-radius:16px;box-shadow:0 24px 80px #17314912}.brand{font-size:13px;letter-spacing:2px;color:#718395}h1{font-size:30px;margin:15px 0}p{font-size:14px;line-height:1.8;color:#64788a}label{display:block;font-size:13px;margin:24px 0 8px}input,button{width:100%;font:inherit;padding:13px;border-radius:8px;border:1px solid #c7d4df}button{margin-top:26px;background:#1b405e;color:#fff;cursor:pointer}#error{color:#a34545;font-size:13px;margin-top:12px;min-height:20px}</style><main class="card"><div class="brand">GUANLAN / PRIVATE RESEARCH</div><h1>观澜 · 云端研究台</h1><p>全市场A股与ETF，近5年历史与技术面分析。<br>使用分配给你的账号登录。</p><form id="form"><label for="user">账号</label><input id="user" autocomplete="username" required maxlength="40"><label for="pass">密码</label><input id="pass" type="password" autocomplete="current-password" required maxlength="128"><button id="submit">登录研究台 →</button><div id="error" role="alert"></div></form></main><script>document.getElementById('form').onsubmit=async e=>{e.preventDefault();const b=document.getElementById('submit');b.disabled=true;try{const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('user').value,password:document.getElementById('pass').value})});if(!r.ok)throw Error((await r.json()).detail||'登录失败');location.href='/';}catch(e){document.getElementById('error').textContent=e.message;b.disabled=false;}};</script></html>'''


@app.get('/login')
def login_page():return HTMLResponse(LOGIN_HTML)


@app.post('/login')
async def login(request:Request):
    if int(request.headers.get('content-length','0'))>2048:raise HTTPException(413,'请求过大')
    body=await request.json();user=str(body.get('username','')).strip();password=str(body.get('password',''))
    # Repeated attempts are throttled per warm instance; gateway also limits per-IP QPS.
    peer=request.client.host if request.client else 'unknown';now=time.time()
    key=peer+'/'+user
    LOGIN_FAILURES[key]=[stamp for stamp in LOGIN_FAILURES[key] if stamp>now-300]
    if len(LOGIN_FAILURES[key])>=8:raise HTTPException(429,'尝试次数过多，请5分钟后再试')
    record=ACCOUNTS.get(user)
    salt=record['salt'] if record else 'invalid-user-salt'
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),200000).hex()
    if not record or not hmac.compare_digest(digest,record['hash']):
        LOGIN_FAILURES[key].append(now);raise HTTPException(401,'账号或密码错误')
    LOGIN_FAILURES.pop(key,None)
    response=JSONResponse({'ok':True,'username':user})
    response.set_cookie(COOKIE,token_for(user),max_age=SESSION_SECONDS,httponly=True,secure=os.environ.get('FINANCE_COOKIE_SECURE','1')=='1',samesite='lax',path='/')
    return response


@app.post('/logout')
def logout(request:Request):
    check_action(request)
    response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/');return response


@app.get('/api/session')
def session(request:Request):return {'username':request.state.user,'mode':'cloud','storage':'CloudBase private storage'}


@app.get('/cloud-ui.js')
def cloud_ui():
    return Response('''(async()=>{const r=await fetch('/api/session');if(!r.ok){location.href='/login';return;}const s=await r.json();const nav=document.querySelector('header nav')||document.querySelector('.gl-header-actions')||document.querySelector('header');if(nav){const wrap=document.createElement('span');wrap.style.cssText='display:inline-flex;align-items:center;gap:10px;font-size:12px;margin-left:10px';const label=document.createElement('span');label.textContent=s.username+' · 云端';const b=document.createElement('button');b.textContent='退出';b.style.cssText='padding:6px 10px;border:1px solid #8b9bb466;border-radius:5px;background:transparent;color:inherit;cursor:pointer';b.onclick=async()=>{await fetch('/logout',{method:'POST',headers:{'x-workbench-action':'local-refresh'}});location.href='/login';};wrap.append(label,b);nav.append(wrap);}window.addEventListener('unhandledrejection',()=>{});})();''',media_type='application/javascript')


def html_page(path):
    value=path.read_text(encoding='utf-8').replace('</body>','<script defer src="/cloud-ui.js"></script></body>')
    if path.name=='index.html' and path.parent.name=='static':
        value=value.replace('本地金融研究工作台','云端金融研究工作台').replace('本地<br>研究台','云端<br>研究台')
        value=value.replace('连接本地数据服务','连接云端数据服务').replace('本地数据','云端数据').replace('本地保存','云端保存').replace('继续补采日线','同步当前日线').replace('开启全市场轮询','开启当前证券轮询')
    return HTMLResponse(value)


@app.get('/')
def home():return html_page(core.ROOT/'app/static/index.html')


@app.get('/technical')
def technical_page():return html_page(core.ROOT/'app/static/technical/index.html')


def technical_result(user):
    pointer=cache_get('users/'+user+'/technical/current.json.gz',ttl=5)
    key=pointer['analysis'] if pointer else 'system/technical/analysis.json.gz'
    result=cache_get(key,ttl=300)
    if result is None:raise HTTPException(404,'尚无云端分析，请先选择证券生成')
    return result,pointer


@app.get('/api/technical')
def analysis(request:Request):return technical_result(request.state.user)[0]


@app.get('/api/technical/status')
def technical_status(request:Request):return {'running':LOCKS['technical/'+request.state.user].locked(),'error':None}


@app.post('/api/technical/refresh')
def technical_refresh(request:Request,body:server.TechnicalSelection|None=None):
    check_action(request);user=request.state.user
    try:watchlist=technical.resolve_watchlist(body.symbols) if body else technical_result(user)[0]['watchlist']
    except ValueError as exc:raise HTTPException(400,str(exc))
    lock=LOCKS['technical/'+user]
    if not lock.acquire(blocking=False):raise HTTPException(409,'该账号正在生成分析')
    try:
        with tempfile.TemporaryDirectory(dir=core.DATA) as temp:
            result=technical.build(output=Path(temp),watchlist=watchlist)
            generation=secrets.token_hex(12);prefix='users/'+user+'/technical/'+generation
            analysis_key=prefix+'/analysis.json.gz';evidence_key=prefix+'/evidence.zip'
            STORE.put_json(analysis_key,result)
            STORE.put(evidence_key,(Path(temp)/'evidence.zip').read_bytes())
            STORE.put_json('users/'+user+'/technical/current.json.gz',{'analysis':analysis_key,'evidence':evidence_key,'generated_at':result['generated_at']})
            with CACHE_LOCK:CACHE.pop('users/'+user+'/technical/current.json.gz',None)
        return {'running':False,'error':None,'finished_at':core.iso()}
    except Exception as exc:
        reference=record_failure('technical',exc)
        raise HTTPException(502,'上游数据或云端保存暂不可用，上一份结果已保留。诊断编号：'+reference)
    finally:lock.release()


@app.get('/static/technical/{filename}')
def technical_download(filename:str,request:Request):
    if filename in ('index.html','technical.css','technical.js'):
        path=core.ROOT/'app/static/technical'/filename
        return html_page(path) if filename.endswith('html') else FileResponse(path)
    result,pointer=technical_result(request.state.user)
    if filename=='analysis.json':return JSONResponse(result)
    if filename=='evidence.zip':
        key=pointer['evidence'] if pointer else 'system/technical/evidence.zip'
        return Response(STORE.get(key),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="evidence.zip"'})
    if filename=='summary.csv':frame=pd.DataFrame([dict(symbol=a['symbol'],name=a['name'],trend=a['trend'],**a['latest']) for a in result['assets']])
    elif filename in ('events.csv','event_statistics.csv'):frame=pd.DataFrame(result[filename[:-4]])
    elif filename in ('correlation.csv','recent60.csv','residual.csv','sample_cov_daily.csv'):
        frame=pd.DataFrame.from_dict(result['comovement'][filename[:-4]],orient='index');frame.index.name='symbol'
        return Response('\ufeff'+frame.to_csv(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
    else:raise HTTPException(404,'文件不存在')
    return Response('\ufeff'+frame.to_csv(index=False),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})


def history(symbol,interval):
    overlay=cache_get('live/'+symbol+'/'+interval+'.json.gz',ttl=60)
    if overlay:return overlay
    entry=CATALOG['entries'].get(symbol+'/'+interval)
    if not entry:return {'records':[],'last_batch':None}
    chunk=cache_get(entry['chunk'],ttl=3600)
    if chunk is None:raise HTTPException(503,'历史数据暂时读取失败')
    result=chunk[symbol+'/'+interval]
    return dict(result,records=[r for r in result['records'] if r['event_time']>=core.history_start()])


@app.get('/api/series/{symbol}')
def series(symbol:str,interval:str='1d',limit:int=2000,source:str='primary'):
    if not core.instrument(symbol):raise HTTPException(404,'证券不在当前名录中')
    if interval not in ('1d','1m','5m'):raise HTTPException(400,'周期不支持')
    if source=='github':return server.series(symbol,interval,limit,source)
    if source!='primary':raise HTTPException(400,'来源不支持')
    item=history(symbol,interval);rows=item['records'][-max(1,min(limit,10000)):]
    with core.db() as c:q=c.execute('SELECT payload FROM market_quotes WHERE symbol=?',(symbol,)).fetchone()
    return dict(instrument=core.instrument(symbol),interval=interval,chart_type='price_line' if interval=='1m' else 'candlestick',records=rows,
        quote=json.loads(q['payload']) if q else None,range={'start':rows[0]['event_time'] if rows else None,'end':rows[-1]['event_time'] if rows else None,'count':len(item['records'])},
        freshness=core.freshness(rows[-1] if rows else None,interval),source='云端行情归档（逐条保留东方财富 / 腾讯来源）',github_available=symbol in github_history.catalog(),
        adjustment='不复权',volume_unit='未核验，仅保留原始量',last_batch=item.get('last_batch'),history_window=core.history_window(),limitations=['分钟历史仅覆盖原始接口可提供的窗口','数据日期以记录标注为准'])


@app.get('/api/coverage')
def coverage():return dict(COVERAGE,history_window=core.history_window(),cloud_snapshot=CATALOG['generated_at'])


@app.get('/api/health')
def health(request:Request):
    preferences=cache_get('users/'+request.state.user+'/preferences.json.gz',ttl=30) or {}
    return dict(mode='cloud',server_time=core.iso(),market=core.calendar_state(),counts={r['interval']:r['rows'] for r in COVERAGE['history']},batches=0,
        auto_refresh={'enabled':bool(preferences.get('auto_refresh')),'interval_seconds':60},job=dict(server.snapshot(),error=None),cloud={'snapshot_at':CATALOG['generated_at'],'storage':'private CloudBase PG Storage'})


@app.get('/api/quality')
def quality(symbol:str|None=None):
    latest=[]
    if symbol and core.instrument(symbol):
        for interval in ['1d','1m','5m']:
            batch=history(symbol,interval).get('last_batch')
            if batch:
                batch=dict(batch);batch['issues']=json.loads(batch.get('issues') or '{}');latest.append(batch)
    return {'latest':latest,'recent':latest,'revision_count':0,'job':server.snapshot()}


@app.get('/api/batches/{batch_id}')
def batch(batch_id:str,symbol:str|None=None):
    if not all(c in '0123456789abcdef' for c in batch_id) or len(batch_id)!=32:raise HTTPException(404,'批次不存在')
    data=cache_get('batches/'+batch_id+'.json.gz')
    if not data:
        index=cache_get('system/batch-metadata.json.gz',ttl=3600) or {}
        data=index.get(batch_id)
        if not data:raise HTTPException(404,'批次不存在')
        return dict(data,issue_examples=[],raw_sample=[],record_examples=[],cloud_note='此归档保存清洗后的行情和原始批次哈希，未复制该批次完整原始响应；新抓取和技术面分析会保存完整证据。')
    raw=STORE.get_json(data['raw_path']);return dict(data,issue_examples=[],raw_sample=raw['rows'][:3])


@app.post('/api/auto-refresh')
def auto_refresh(request:Request,body:server.AutoRefresh):
    check_action(request)
    key='users/'+request.state.user+'/preferences.json.gz'
    STORE.put_json(key,{'auto_refresh':body.enabled})
    with CACHE_LOCK:CACHE.pop(key,None)
    return {'enabled':body.enabled,'interval_seconds':60,'mode':'browser'}


@app.post('/api/backfill')
def backfill(request:Request,body:server.Refresh):
    return refresh_series(request,server.Refresh(symbols=body.symbols,intervals=['1d']))


def sync_quotes():
    global QUOTE_VERSION
    snapshot=cache_get('live/market-quotes.json.gz',ttl=60)
    if snapshot and QUOTE_VERSION!=snapshot['fetched_at']:
        with core.db() as c:
            c.executemany('INSERT OR REPLACE INTO market_quotes VALUES(?,?,?,?)',[(s,core.dumps(q),'cloud-'+snapshot['fetched_at'],snapshot['fetched_at']) for s,q in snapshot['items'].items()])
        QUOTE_VERSION=snapshot['fetched_at']


@app.get('/api/universe')
def universe(q:str='',kind:str='all',exchange:str='all',offset:int=0,limit:int=50,sort:str='code'):
    sync_quotes()
    return server.universe(q,kind,exchange,offset,limit,sort)


@app.post('/api/market-refresh')
def refresh_market(request:Request):
    check_action(request)
    lock=LOCKS['market']
    if not lock.acquire(blocking=False):raise HTTPException(409,'全市场快照正在更新')
    try:
        snapshot=tencent_source.market_quotes(core.universe())
        STORE.put_json('live/market-quotes.json.gz',snapshot)
        with CACHE_LOCK:CACHE.pop('live/market-quotes.json.gz',None)
        sync_quotes()
        return dict(server.snapshot(),running=False,mode='cloud',updated=len(snapshot['items']))
    except Exception as exc:
        reference=record_failure('market',exc)
        raise HTTPException(502,'全市场行情源暂不可用，旧快照保留。诊断编号：'+reference)
    finally:lock.release()


@app.post('/api/refresh')
def refresh_series(request:Request,body:server.Refresh):
    check_action(request)
    symbols=body.symbols
    if not symbols or len(symbols)>12 or any(not core.instrument(s) for s in symbols):raise HTTPException(400,'请选择1—12只有效证券')
    if not body.intervals or any(i not in ('1d','1m','5m') for i in body.intervals):raise HTTPException(400,'周期不支持')
    lock=LOCKS['refresh/'+request.state.user]
    if not lock.acquire(blocking=False):raise HTTPException(409,'该账号已有更新任务')
    try:
        for symbol in dict.fromkeys(symbols):
            for interval in dict.fromkeys(body.intervals):
                tencent_source.refresh_series(symbol,interval,history(symbol,interval),STORE)
                with CACHE_LOCK:CACHE.pop('live/'+symbol+'/'+interval+'.json.gz',None)
        return dict(server.snapshot(),running=False,mode='cloud',finished_at=core.iso())
    except Exception as exc:
        reference=record_failure('series',exc)
        raise HTTPException(502,'行情源或云存储暂不可用，原数据保留。诊断编号：'+reference)
    finally:lock.release()


# Read-only routes (inventory/search/static assets) reuse the tested local workbench.
owned={r.path for r in app.routes}
for route in server.app.routes:
    if route.path not in owned and route.path not in ('/api/market-refresh','/api/refresh','/api/auto-refresh','/static'):
        app.router.routes.append(route)
app.mount('/static',server.StaticFiles(directory=core.ROOT/'app/static'),name='cloud-static')
