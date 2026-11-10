"""Pinned public GitHub CSV history, separate from the primary price store."""
import csv
import hashlib
import io
import json
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
import requests
from . import core,market

REPO='tomuopc/a-stock-data'
COMMIT='cd1ee3a2e2c28ba5b623b159e8444130ca7546e1'
GUARD=threading.Lock();DOWNLOADS=threading.BoundedSemaphore(2)

@lru_cache(maxsize=1)
def catalog():
    data=json.loads((core.ROOT/'research/github-data/tdx-tree.json').read_text(encoding='utf-8'))
    if data['commit']!=COMMIT or data['tree'].get('truncated'):raise ValueError('Unpinned or incomplete GitHub catalog')
    result={}
    for r in data['tree']['tree']:
        if r['type']!='blob' or not r['path'].endswith('.csv'):continue
        kind='stock' if r['path'].startswith('stock/stock-data/') else 'etf' if r['path'].startswith('fund/fund-data/') else None
        if not kind:continue
        code=Path(r['path']).name[:6]
        try:symbol=code+'.'+market.exchange(code,kind)
        except ValueError:continue
        if symbol in result:raise ValueError('Ambiguous GitHub code '+symbol)
        result[symbol]=dict(r,kind=kind,symbol=symbol)
    return result


def verify(content,spec):
    digest=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
    if len(content)!=spec['size'] or digest!=spec['sha']:raise ValueError('GitHub pinned blob identity mismatch')
    return hashlib.sha256(content).hexdigest()


def ensure_file(symbol):
    spec=catalog().get(symbol)
    if not spec:return None
    folder=core.DATA/'external/github-csv'/COMMIT;folder.mkdir(parents=True,exist_ok=True)
    target=folder/(symbol+'.csv')
    with DOWNLOADS:
        if target.exists():verify(target.read_bytes(),spec);return target
        if spec['size']>5_000_000:raise ValueError('Source file exceeds permitted size')
        url='https://raw.githubusercontent.com/'+REPO+'/'+COMMIT+'/'+quote(spec['path'],safe='/')
        content=bytearray()
        with requests.get(url,stream=True,timeout=(10,30)) as response:
            response.raise_for_status()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content)>spec['size']:raise ValueError('Unexpected download size')
        digest=verify(content,spec)
        # Atomic replacement; concurrent reads can only see a complete verified blob.
        temp=folder/(symbol+'.'+core.uuid.uuid4().hex+'.part');temp.write_bytes(content);temp.replace(target)
        (folder/(symbol+'.json')).write_text(core.dumps({'repository':'https://github.com/'+REPO,'commit':COMMIT,'path':spec['path'],'git_blob_sha1':spec['sha'],'sha256':digest,'downloaded_at':core.iso(),'license':'No standalone license declared; local research copy; not redistributed'}), encoding='utf-8')
    return target


def parse(content,symbol,fetched_at):
    reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    required={'time','open','close','high','low','vol'}
    if not required.issubset(reader.fieldnames or []):raise ValueError('Unknown historical CSV schema')
    records={};conflicts=set();rejected=0;outside=0
    cutoff=core.history_start(core.datetime.fromisoformat(fetched_at))
    for raw in reader:
        if str(raw.get('time',''))<cutoff:
            outside+=1;continue
        mapped={v:raw.get(k) for k,v in [('time','日期'),('open','开盘'),('close','收盘'),('high','最高'),('low','最低'),('vol','成交量')]}
        row,_=core.normalize(mapped,symbol,'1d',fetched_at)
        if row is None:rejected+=1;continue
        row.update(source='GitHub / tomuopc/a-stock-data / 通达信',adjustment='source_unspecified',fetched_at=fetched_at,
                   batch_id='github:'+COMMIT+':'+symbol,flags=['adjustment_not_declared'])
        key=row['event_time']
        if key in records and records[key]!=row:conflicts.add(key)
        else:records[key]=row
    for key in conflicts:records.pop(key,None)
    return sorted(records.values(),key=lambda r:r['event_time']),{'invalid_rows':rejected,'conflicting_dates':len(conflicts),'excluded_before_window':outside}


def audit(records,symbol):
    current={r['event_time']:r for r in core.observations(symbol,'1d',10000)}
    tolerance=.0011 if core.instrument(symbol)['kind']=='etf' else .0101
    common=0;mismatches=[]
    for r in records:
        local=current.get(r['event_time'])
        if not local:continue
        common+=1
        if any(abs(r[k]-local[k])>tolerance for k in ('open','high','low','close')):
            mismatches.append({'date':r['event_time'][:10],'github_close':r['close'],'primary_close':local['close']})
    return {'compared_days':common,'ohlc_mismatch_days':len(mismatches),'examples':mismatches[:5],'tolerance_cny':tolerance,'audited_at':core.iso(),
            'note':'重叠窗口价格比较不证明早期历史完整性或全部复权口径'}


@lru_cache(maxsize=12)
def loaded(data_dir,symbol,mtime,cutoff):
    path=Path(data_dir)/'external/github-csv'/COMMIT/(symbol+'.csv')
    fetched=core.datetime.fromtimestamp(mtime,core.timezone.utc).isoformat()
    content=path.read_bytes();spec=catalog()[symbol];sha=verify(content,spec)
    records,quality=parse(content,symbol,core.iso())
    for row in records:row['fetched_at']=fetched
    checked=audit(records,symbol)
    result={'records':records,'quality':quality,'audit':checked,'sha256':sha,'source_path':spec['path']}
    (path.with_suffix('.audit.json')).write_text(core.dumps({k:v for k,v in result.items() if k!='records'}), encoding='utf-8')
    return result


def series(symbol,limit=10000):
    path=ensure_file(symbol)
    if not path:return None
    result=loaded(str(core.DATA),symbol,path.stat().st_mtime,core.history_start())
    records=result['records'];rows=records[-limit:]
    return {'instrument':core.instrument(symbol),'interval':'1d','chart_type':'candlestick','records':rows,
            'range':{'start':records[0]['event_time'] if records else None,'end':records[-1]['event_time'] if records else None,'count':len(records)},
            'source':'GitHub / tomuopc/a-stock-data / 通达信','adjustment':'源仓库未声明复权口径；独立展示',
            'freshness':{'state':'archive','label':'GitHub 历史归档'},'last_batch':None,'volume_unit':'源未核验',
            'provenance':{'repository':'https://github.com/'+REPO,'commit':COMMIT,'path':result['source_path'],'sha256':result['sha256']},
            'history_window':core.history_window(),'audit':audit(records,symbol),'quality':result['quality'],'limitations':['归档截止2026-06-26，非最新行情','没有自动覆盖主行情库','重叠窗口核对不代表全部历史真实性或完整性已验证']}


def summary():
    known=core.universe();available=[s for s in catalog() if s in known]
    folder=core.DATA/'external/github-csv'/COMMIT
    cached=list(folder.glob('*.csv')) if folder.exists() else []
    return {'repo':'https://github.com/'+REPO,'commit':COMMIT,'available_current_symbols':len(available),
            'available_stocks':sum(known[s]['kind']=='stock' for s in available),'available_etfs':sum(known[s]['kind']=='etf' for s in available),
            'cached_symbols':len(cached),'cutoff':'2026-06-26','history_window':core.history_window(),'mode':'独立历史源，仅近5年，按需下载，不计入主库覆盖'}
