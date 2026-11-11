"""Export five-year history into private immutable CloudBase Storage chunks."""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import core,market,github_history
from app.cloud_store import CloudStore


def export(source,output):
    output.mkdir(parents=True,exist_ok=True)
    core.DATA=source
    source_connection=sqlite3.connect(source/'workbench.sqlite');source_connection.row_factory=sqlite3.Row
    seed=output/'seed.sqlite';seed.unlink(missing_ok=True)
    target=sqlite3.connect(seed)
    names=['instruments','market_quotes','market_members','history_tasks','metadata']
    for name in names:
        ddl=source_connection.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?',(name,)).fetchone()[0]
        target.execute(ddl)
        rows=source_connection.execute('SELECT * FROM '+name).fetchall()
        target.executemany('INSERT INTO '+name+' VALUES('+','.join('?' for _ in rows[0])+')',[tuple(r) for r in rows])
    target.commit();target.close()
    seed_content=gzip.compress(seed.read_bytes(),mtime=0);(output/'seed.sqlite.gz').write_bytes(seed_content)
    coverage=market.coverage()
    coverage['github_history']=github_history.summary()
    (output/'coverage.json.gz').write_bytes(gzip.compress(core.dumps(coverage).encode(),mtime=0))
    snapshot='history/'+core.now().strftime('%Y%m%dT%H%M%S')+'-'+core.uuid.uuid4().hex[:8];entries={};chunk={};chunk_no=0;total=0;bytes_total=0
    pairs=source_connection.execute('SELECT DISTINCT symbol,interval FROM observations ORDER BY symbol,interval').fetchall()
    for count,pair in enumerate(pairs,1):
        symbol,interval=pair
        rows=source_connection.execute('SELECT payload,batch_id,fetched_at FROM observations WHERE symbol=? AND interval=? AND event_time>=? ORDER BY event_time',(symbol,interval,core.history_start())).fetchall()
        records=[dict(core.unpack_payload(r['payload']),batch_id=r['batch_id'],fetched_at=r['fetched_at']) for r in rows]
        if not records:continue
        batch=source_connection.execute('SELECT * FROM batches WHERE id=?',(rows[-1]['batch_id'],)).fetchone()
        content={'records':records,'last_batch':dict(batch) if batch else None}
        key=symbol+'/'+interval;chunk[key]=content
        entries[key]={'chunk':snapshot+'/chunks/'+str(chunk_no).zfill(4)+'.json.gz','count':len(records),'start':records[0]['event_time'],'end':records[-1]['event_time']}
        total+=len(records)
        if len(chunk)>=16 or count==len(pairs):
            path=output/'chunks'/(str(chunk_no).zfill(4)+'.json.gz');path.parent.mkdir(exist_ok=True)
            content=gzip.compress(json.dumps(chunk,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode(),compresslevel=6,mtime=0)
            path.write_bytes(content);bytes_total+=len(content);chunk={};chunk_no+=1
            if chunk_no%20==0:print(f'Exported {count}/{len(pairs)} series, {total:,} records, {bytes_total/1024**2:.1f} MiB',flush=True)
    catalog={'schema_version':1,'prefix':snapshot,'generated_at':core.iso(),'window_start':core.history_start(),'entries':entries,'records':total,'chunks':chunk_no,'compressed_bytes':bytes_total}
    (output/'catalog.json.gz').write_bytes(gzip.compress(core.dumps(catalog).encode(),mtime=0))
    (output/'export.json').write_text(core.dumps(catalog),encoding='utf-8')
    print('Export complete',total,'records',chunk_no,'chunks',round(bytes_total/1024**2,1),'MiB',flush=True)


def upload(output,store):
    catalog=json.loads(gzip.decompress((output/'catalog.json.gz').read_bytes()));prefix=catalog['prefix']
    checkpoint=output/'uploaded.json';done=set(json.loads(checkpoint.read_text())) if checkpoint.exists() else set()
    paths=list(sorted((output/'chunks').glob('*.gz')))+[output/'seed.sqlite.gz',output/'coverage.json.gz',output/'catalog.json.gz']
    def put(path):
        key=prefix+'/'+path.relative_to(output).as_posix()
        if key in done:return key
        for attempt in range(3):
            try:store.put(key,path.read_bytes());return key
            except Exception:
                if attempt==2:raise
                time.sleep(1+attempt)
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending={pool.submit(put,path) for path in paths}
        while pending:
            finished,pending=wait(pending,timeout=15,return_when=FIRST_COMPLETED)
            for future in finished:done.add(future.result())
            checkpoint.write_text(json.dumps(sorted(done)))
            if len(done)%20==0 or not pending:print(f'Uploaded {len(done)}/{len(paths)} objects',flush=True)
    store.put_json('system/current.json.gz',{'prefix':prefix,'catalog':prefix+'/catalog.json.gz','seed':prefix+'/seed.sqlite.gz','coverage':prefix+'/coverage.json.gz'})
    print('Cloud snapshot activated',prefix,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--key-file',type=Path,required=True);parser.add_argument('--env-id',required=True);parser.add_argument('--upload-only',action='store_true');args=parser.parse_args()
    if not args.upload_only:export(args.source,args.output)
    key=json.loads(args.key_file.read_text())['data']['ApiKey']
    upload(args.output,CloudStore(env=args.env_id,key=key))
