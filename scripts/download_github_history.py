"""Download a pinned, checksum-verified public research dataset; never execute repository code."""
import hashlib,json,requests,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'data/external/a-share-data';out.mkdir(parents=True,exist_ok=True)
url='https://github.com/noblechang/a-share-data/releases/download/FREE_DATA_v0/ashare_pit.duckdb'
expected='95601e2f1f5b62595a80e735a52da3a64c7446ebe7f8c984e3394b96f90e4778'
target=out/'ashare_pit.duckdb';partial=target.with_suffix('.part')
if not target.exists():
 size=partial.stat().st_size if partial.exists() else 0
 if size>1001402368:raise ValueError('Partial asset exceeds expected size')
 started=time.monotonic();report=size
 if size<1001402368:
  headers={'Range':f'bytes={size}-'} if size else {}
  with requests.get(url,headers=headers,stream=True,timeout=(10,60)) as response:
   response.raise_for_status()
   if size and response.status_code==206:
    if not response.headers.get('Content-Range','').startswith(f'bytes {size}-'):raise ValueError('Unexpected range response')
   elif size:size=0
   with partial.open('ab' if size else 'wb') as f:
    for chunk in response.iter_content(1024*1024):
     if not chunk:continue
     size+=len(chunk)
     if size>1001402368:raise ValueError('Unexpected asset size')
     f.write(chunk)
     if size-report>=128*1024*1024:print(json.dumps({'bytes':size,'seconds':round(time.monotonic()-started,1)}),flush=True);report=size
 digest=hashlib.sha256()
 with partial.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
 if size!=1001402368 or digest.hexdigest()!=expected:raise ValueError('Release checksum / size mismatch')
 partial.rename(target)
else:
 digest=hashlib.sha256()
 with target.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
 if digest.hexdigest()!=expected:raise ValueError('Existing file checksum mismatch')
manifest={'repository':'https://github.com/noblechang/a-share-data','release':'FREE_DATA_v0','asset_url':url,'sha256':expected,'bytes':target.stat().st_size,'license_note':'Publisher declares learning/research use; no standalone license file','usage':'local read-only research, separate source; not redistributed','downloaded_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
(out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print(json.dumps(manifest,ensure_ascii=False),flush=True)
