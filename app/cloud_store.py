"""Private CloudBase PG Storage adapter; server credentials never reach browsers."""
import gzip
import json
import os
from urllib.parse import quote
import requests

class CloudStore:
    def __init__(self,env=None,key=None,bucket=None):
        self.base='https://'+(env or os.environ['CLOUDBASE_ENV_ID'])+'.api.tcloudbasegateway.com'
        self.key=key or os.environ['CLOUDBASE_APIKEY']
        self.bucket=bucket or os.environ.get('FINANCE_CLOUD_BUCKET','oldpig-finance')
    def _request(self,method,path,**kwargs):
        r=requests.request(method,self.base+path,headers={'Authorization':'Bearer '+self.key,**kwargs.pop('headers',{})},timeout=(8,90),**kwargs)
        if not r.ok:
            # Do not include response bodies, signed URLs, headers or credential-bearing URLs.
            raise RuntimeError('Cloud storage request failed: HTTP '+str(r.status_code))
        return r
    def create_bucket(self):
        return self._request('POST','/v1/storages/bucket/',json={'id':self.bucket,'name':self.bucket,'public':False,'file_size_limit':52428800}).json()
    def put(self,key,content):
        path='/v1/storages/object/'+quote(self.bucket,safe='')+'/'+quote(key,safe='/')
        return self._request('PUT',path,data=content,headers={'Content-Type':'application/octet-stream','Content-Length':str(len(content))}).json()
    def get(self,key,optional=False):
        path='/v1/storages/object/sign/'+quote(self.bucket,safe='')+'/'+quote(key,safe='/')
        r=requests.post(self.base+path,headers={'Authorization':'Bearer '+self.key},json={'expiresIn':120},timeout=(8,30))
        if optional and r.status_code in (400,404):
            try:
                body=r.json()
                if r.status_code==404 or any(x in str(body).lower() for x in ['not found','not exist','不存在']):return None
            except ValueError:pass
        if not r.ok:raise RuntimeError('Cloud storage sign failed: HTTP '+str(r.status_code))
        value=r.json();url=value.get('fullSignedURL') or value.get('signedURL')
        if not url:raise RuntimeError('Cloud storage returned no download URL')
        if url.startswith('/'):url=self.base+url
        if not url.startswith('https://'):raise RuntimeError('Insecure cloud download URL')
        data=requests.get(url,timeout=(8,90))
        if optional and data.status_code==404:return None
        if not data.ok:raise RuntimeError('Cloud storage download failed: HTTP '+str(data.status_code))
        return data.content
    def put_json(self,key,value):
        return self.put(key,gzip.compress(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode(),mtime=0))
    def get_json(self,key,optional=False):
        content=self.get(key,optional)
        return json.loads(gzip.decompress(content)) if content is not None else None
    def list_keys(self,prefix):
        """Documented PG Storage v2 listing; immutable experiment objects avoid lost indexes."""
        keys=[];cursor=None
        while True:
            body={'prefix':prefix,'limit':1000,'with_delimiter':False,'sortBy':{'column':'name','order':'desc'}}
            if cursor:body['cursor']=cursor
            page=self._request('POST','/v1/storages/object/list/'+quote(self.bucket,safe=''),json=body).json()
            keys.extend(item.get('key') or item['name'] for item in page.get('objects',[]))
            if not page.get('hasNext'):return keys
            next_cursor=page.get('nextCursor')
            if not next_cursor or next_cursor==cursor:raise RuntimeError('Cloud storage pagination cursor missing')
            cursor=next_cursor
