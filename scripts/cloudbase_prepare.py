"""Prepare the cloud package and private deployment configuration; dependencies must already be installed."""
import json,os,secrets,hashlib,shutil,gzip,base64
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description="Prepare a CloudBase HTTP function without putting credentials in Git")
parser.add_argument("--build-dir",type=Path,required=True)
parser.add_argument("--private-dir",type=Path,required=True)
parser.add_argument("--env-id",required=True)
parser.add_argument("--origin",required=True)
args=parser.parse_args()
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.cloud_store import CloudStore
root=Path(__file__).resolve().parents[1];build=args.build_dir.resolve();private=args.private_dir.resolve()
private.mkdir(parents=True,exist_ok=True);private.chmod(0o700)
key=json.loads((private/'runtime-key.json').read_text())['data']['ApiKey']
store=CloudStore(env=args.env_id,key=key)
store.put_json('system/technical/analysis.json.gz',json.loads((root/'app/static/technical/analysis.json').read_text()))
store.put('system/technical/evidence.zip',(root/'app/static/technical/evidence.zip').read_bytes())
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
shutil.copytree(root/'research/github-data',fn/'research/github-data',dirs_exist_ok=True)
(fn/'scf_bootstrap').write_text('#!/bin/bash\ncd /var/user\nexport PYTHONPATH=/var/user\nexec /var/lang/python310/bin/python3.10 -m uvicorn app.cloud_server:app --host 0.0.0.0 --port 9000 --no-access-log\n')
(fn/'scf_bootstrap').chmod(0o755)
# Strip caches inside vendored dependencies.
for path in fn.rglob('__pycache__'):shutil.rmtree(path)
print('Package prepared; secrets stored only in private deployment configuration.')
