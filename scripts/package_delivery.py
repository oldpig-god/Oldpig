"""Build a ZIP64 handoff. Stop the server and all collectors before invoking.

Streams the large SQLite file, so no second uncompressed DB copy is required.
The exclusive SQLite transaction protects the checkpointed source during packing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
TOP='finance-workbench'


def included(path):
    relative=path.relative_to(ROOT)
    if any(part in {'.venv','__pycache__','.pytest_cache','node_modules','.git'} for part in relative.parts):return False
    if relative.name.endswith(('.pyc','.lock','.log','.tmp','.part')):return False
    if relative.as_posix()=='delivery-manifest.json':return False
    if relative.parts[0]=='data':
        return relative.as_posix()=='data/workbench.sqlite' or len(relative.parts)>1 and relative.parts[1] in {'raw','research','discovery'}
    return relative.parts[0] in {'app','scripts','tests','design','research','contracts','frontend'} or relative.name in {
        'README.md','foragent.md','.gitignore','requirements.txt','requirements-dev.txt','requirements-research.txt',
        'setup-windows.cmd','start-windows.cmd','setup-linux.sh','start-linux.sh'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise FileExistsError(output)
    temporary=output.with_suffix('.zip.partial')
    if temporary.exists():raise FileExistsError(temporary)
    files=sorted(p for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink() and included(p))
    # Largest entry first: detect disk shortage before spending time on small files.
    files.sort(key=lambda p:(p.name!='workbench.sqlite',p.as_posix()))
    manifest=dict(format_version=1,created_at=datetime.now(timezone.utc).isoformat(),top_directory=TOP,
        uncompressed_bytes=sum(p.stat().st_size for p in files),files=[],
        excluded=['.venv and installed dependencies','data/external (GitHub cache, quarantined samples, incomplete download)',
                  '*.lock / *.log / caches / SQLite WAL and SHM after checkpoint'],
        database_consistency='WAL checkpoint(TRUNCATE) followed by exclusive SQLite transaction; writers stopped',
        platform_validation='Linux execution tested; Windows compatibility implemented, native Windows acceptance still required')
    connection=sqlite3.connect(ROOT/'data/workbench.sqlite',timeout=30)
    try:
        checkpoint=connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint[0]!=0:raise RuntimeError('Database checkpoint busy; stop every writer first')
        connection.execute('PRAGMA locking_mode=EXCLUSIVE')
        connection.execute('BEGIN EXCLUSIVE')
        running=connection.execute("SELECT count(*) FROM history_tasks WHERE status='running'").fetchone()[0]
        if running:raise RuntimeError('History tasks are still running')
        manifest['database_queue']=dict(connection.execute('SELECT status,count(*) FROM history_tasks GROUP BY status'))
        before=time.monotonic();last=before
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9,allowZip64=True) as archive:
            for number,path in enumerate(files,1):
                relative=path.relative_to(ROOT).as_posix();digest=hashlib.sha256();size=path.stat().st_size
                info=zipfile.ZipInfo.from_file(path,arcname=TOP+'/'+relative)
                info.compress_type=zipfile.ZIP_DEFLATED
                info._compresslevel=9 if path.name=='workbench.sqlite' else 6
                read=0
                with path.open('rb') as source,archive.open(info,'w',force_zip64=True) as target:
                    while chunk:=source.read(8*1024*1024):
                        if shutil.disk_usage(output.parent).free<128*1024*1024:
                            raise OSError('Disk reserve under 128 MiB; incomplete archive will be removed')
                        target.write(chunk);digest.update(chunk);read+=len(chunk)
                        if time.monotonic()-last>25:
                            print(json.dumps(dict(file=relative,read_bytes=read,total_bytes=size,files_done=number-1,
                                archive_bytes=temporary.stat().st_size,elapsed=round(time.monotonic()-before)),ensure_ascii=False),flush=True);last=time.monotonic()
                manifest['files'].append(dict(path=relative,bytes=size,sha256=digest.hexdigest()))
            encoded=json.dumps(manifest,ensure_ascii=False,indent=2)
            archive.writestr(TOP+'/delivery-manifest.json',encoded.encode('utf-8'))
        (ROOT/'delivery-manifest.json').write_text(encoded,encoding='utf-8')
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.rollback();connection.close()
    # Validate CRC and SHA in a single decompression pass without extracting another 10GB.
    expected={TOP+'/'+f['path']:f['sha256'] for f in manifest['files']}
    with zipfile.ZipFile(output) as archive:
        for index,info in enumerate(archive.infolist(),1):
            digest=hashlib.sha256()
            with archive.open(info) as source:
                for chunk in iter(lambda:source.read(8*1024*1024),b''):digest.update(chunk)
            if info.filename in expected and digest.hexdigest()!=expected[info.filename]:raise RuntimeError('Archive content mismatch: '+info.filename)
            if time.monotonic()-last>25:
                print(f'Verified {index}/{len(archive.infolist())}: {info.filename}',flush=True);last=time.monotonic()
    digest=hashlib.sha256()
    with output.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
    output.with_suffix('.zip.sha256').write_text(digest.hexdigest()+'  '+output.name+'\n',encoding='ascii')
    result=dict(zip=str(output),bytes=output.stat().st_size,uncompressed_bytes=manifest['uncompressed_bytes'],
                files=len(manifest['files'])+1,sha256=digest.hexdigest(),verified_crc_and_sha256=True)
    output.with_suffix('.zip.validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
