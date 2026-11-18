"""Prepare the cloud package and private deployment configuration; dependencies must already be installed."""
import json,os,secrets,hashlib,shutil,gzip,base64,sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description="Prepare a CloudBase HTTP function without putting credentials in Git")
parser.add_argument("--build-dir",type=Path,required=True)
parser.add_argument("--private-dir",type=Path,required=True)
parser.add_argument("--env-id",required=True)
parser.add_argument("--origin",required=True)
parser.add_argument("--source-db",type=Path,default=Path(os.environ.get("FINANCE_SOURCE_DB","/home/xietianyou/work/finance-workbench/data/workbench.sqlite")))
args=parser.parse_args()
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import bars,core,daily_sync,screener
from app.cloud_store import CloudStore
root=Path(__file__).resolve().parents[1];build=args.build_dir.resolve();private=args.private_dir.resolve()
private.mkdir(parents=True,exist_ok=True);private.chmod(0o700)
key=json.loads((private/'runtime-key.json').read_text())['data']['ApiKey']
store=CloudStore(env=args.env_id,key=key)
store.put_json('system/technical/analysis.json.gz',json.loads((root/'app/static/technical/analysis.json').read_text()))
store.put('system/technical/evidence.zip',(root/'app/static/technical/evidence.zip').read_bytes())
if args.source_db.exists():
 src=sqlite3.connect(args.source_db.as_uri()+'?mode=ro',uri=True,timeout=20)
 src.row_factory=sqlite3.Row
 try:
  screen_row=src.execute('SELECT payload FROM screen_runs ORDER BY created_at DESC LIMIT 1').fetchone()
  if screen_row:
   payload=json.loads(screen_row['payload'])
   store.put_json('system/screen/latest.json.gz',payload)
   if payload.get('batch_id'):
    b=src.execute('SELECT * FROM batches WHERE id=?',(payload['batch_id'],)).fetchone()
    if b:
     bd=dict(b);bd['raw_path']=None
     store.put_json('batches/'+bd['id']+'.json.gz',bd)
   quotes={}
   max_fetched=''
   for qr in src.execute('SELECT symbol,payload,fetched_at FROM market_quotes').fetchall():
    qp=json.loads(qr['payload']);quotes[qr['symbol']]=qp
    if (qr['fetched_at'] or '')>max_fetched:max_fetched=qr['fetched_at']
   if quotes:
    store.put_json('live/market-quotes.json.gz',{'fetched_at':max_fetched or core.iso(),'items':quotes,'source':'Eastmoney'})
   passed=[i['symbol'] for i in payload.get('items',[]) if screener.passes(i.get('checks',{}),True)]
   cutoff=core.ts_of('2026-11-01')
   bar_uploads=[]
   for sym in passed:
    brows=src.execute(f'SELECT b.{bars.COLUMNS.replace(",",",b.")},f.batch_id,f.fetched_at FROM bars b JOIN batch_refs f ON f.id=b.ref WHERE b.symbol=? AND b.interval="1d" AND b.ts>=? ORDER BY b.ts',(sym,cutoff)).fetchall()
    recs=[dict(bars.to_payload(r,core.SH),batch_id=r['batch_id'],fetched_at=r['fetched_at']) for r in brows]
    if not recs:continue
    lb=src.execute('SELECT * FROM batches WHERE symbol=? AND interval="1d" ORDER BY started_at DESC LIMIT 1',(sym,)).fetchone()
    lbd=dict(lb) if lb else None
    if lbd:lbd['raw_path']=None
    bar_uploads.append((sym,recs,lbd))
   def upload_series(item):
    sym,recs,lbd=item
    prev=store.get_json('live/'+sym+'/1d.json.gz',optional=True)
    merged=daily_sync.merge_history(prev,{'records':recs,'last_batch':lbd})
    if lbd:store.put_json('batches/'+lbd['id']+'.json.gz',lbd)
    store.put_json('live/'+sym+'/1d.json.gz',merged)
   with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(upload_series,bar_uploads))
   print(f'Synced screen ({len(passed)} passed), {len(quotes)} quotes, {len(bar_uploads)} daily overlays to CloudStore.')
 finally:
  src.close()
credential_path=private/'accounts.json'
if credential_path.exists():credentials=json.loads(credential_path.read_text())
else:
 credentials={name:secrets.token_urlsafe(15) for name in ['owner','researcher1','researcher2','researcher3']}
 credential_path.write_text(json.dumps(credentials));credential_path.chmod(0o600)
config_path=private/'deploy.json'
if config_path.exists():old=json.loads(config_path.read_text());env=old['functions'][0]['envVariables']
else:env={'FINANCE_SESSION_KEY':secrets.token_hex(32)}
accounts={}
for name,password in credentials.items():
 salt=secrets.token_hex(16);accounts[name]={'salt':salt,'hash':hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),200000).hex()}
origin=args.origin.rstrip('/')
env.pop('FINANCE_CLOUD_USERS',None)
env.update({'CLOUDBASE_ENV_ID':args.env_id,'CLOUDBASE_APIKEY':key,'FINANCE_CLOUD_BUCKET':'oldpig-finance','FINANCE_DATA_DIR':'/tmp/oldpig-data','FINANCE_PUBLIC_ORIGIN':origin,'FINANCE_CLOUD_USERS_B64':base64.b64encode(json.dumps(accounts).encode()).decode(),'FINANCE_TECH_PROVIDER':'tencent','FINANCE_SOURCE_WORKERS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'})
config={'envId':args.env_id,'functionRoot':str(build),'functions':[{'name':'guanlan-api','dir':'function','runtime':'Python3.10','handler':'index.main','isHTTP':True,'type':'HTTP','timeout':120,'initTimeout':90,'memorySize':1024,'installDependency':False,'envVariables':env,'ignore':['**/__pycache__/**','**/tests/**','**/*.pyc']}]}
config_path.write_text(json.dumps(config));config_path.chmod(0o600)
fn=build/'function'
if (fn/'app').exists():shutil.rmtree(fn/'app')
shutil.copytree(root/'app',fn/'app',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
# Data lives in private storage, not in the immutable function package.
for path in (fn/'app/static/technical').iterdir():
 if path.suffix in ('.json','.csv','.zip'):path.unlink()
(fn/'scf_bootstrap').write_text('#!/bin/bash\ncd /var/user\nexport PYTHONPATH=/var/user\nexec /var/lang/python310/bin/python3.10 -m uvicorn app.cloud_server:app --host 0.0.0.0 --port 9000 --no-access-log\n')
(fn/'scf_bootstrap').chmod(0o755)
# Strip caches inside vendored dependencies.
for path in fn.rglob('__pycache__'):shutil.rmtree(path)
print('Package prepared; secrets stored only in private deployment configuration.')
