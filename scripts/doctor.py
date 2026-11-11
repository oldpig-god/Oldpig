"""Offline environment diagnosis; does not initialize, clear or migrate the DB."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import struct
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--deep',action='store_true');parser.add_argument('--technical-only',action='store_true');args=parser.parse_args()
    result=dict(python=sys.version,executable=sys.executable,platform=platform.platform(),bits=struct.calcsize('P')*8,
                root=str(ROOT),dependencies={},errors=[],warnings=[])
    if sys.version_info < (3,10):result['errors'].append('Python 3.10+ required; recommend 3.11 x64')
    if result['bits']!=64:result['errors'].append('64-bit Python required for this large dataset')
    for package in ['akshare','fastapi','uvicorn','pandas','requests','tzdata','numpy','scipy','scikit-learn','matplotlib']:
        try:result['dependencies'][package]=metadata.version(package)
        except metadata.PackageNotFoundError:result['errors'].append('Missing dependency: '+package)
    try:result['shanghai_now']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    except Exception as exc:result['errors'].append('Timezone error: '+str(exc))
    data=Path(os.environ.get('FINANCE_DATA_DIR',ROOT/'data')).resolve();result['data']=str(data)
    path=data/'workbench.sqlite'
    if args.technical_only:result['database_check']='Skipped: six-stock research uses the portable snapshot'
    elif not path.exists():result['errors'].append('Delivered database missing; verify extraction and FINANCE_DATA_DIR before starting')
    else:
        result['database_bytes']=path.stat().st_size
        try:
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10) as c:
                tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                missing={'instruments','observations','batches','metadata','history_tasks'}-tables
                if missing:result['errors'].append('Missing tables: '+str(missing))
                else:
                    result['active_instruments']=c.execute('SELECT count(*) FROM instruments WHERE active=1').fetchone()[0]
                    result['queue']=dict(c.execute('SELECT status,count(*) FROM history_tasks GROUP BY status'))
                if args.deep:
                    integrity=[r[0] for r in c.execute('PRAGMA quick_check')];result['quick_check']=integrity
                    if integrity!=['ok']:result['errors'].append('SQLite quick_check failed')
        except Exception as exc:result['errors'].append('Database read error: '+str(exc))
    research=ROOT/'app/static/research/analysis.json'
    try:
        r=json.loads(research.read_text(encoding='utf-8'))
        result['research']=dict(generated_at=r['generated_at'],observations=r['observations'],pairs=len(r['pairs']))
    except Exception as exc:result['errors'].append('Research artifact error: '+str(exc))
    if args.technical_only:
        try:
            from app import technical
            manifest=json.loads((technical.PORTABLE/'manifest.json').read_text(encoding='utf-8'))
            frames,quality=technical.load_frames(technical.PORTABLE,manifest)
            result['technical_snapshot']=dict(series=len(frames),evidence_hashes_verified=True,expected_end=manifest['expected_end'])
        except Exception as exc:result['errors'].append('Technical snapshot error: '+str(exc))
    elif not (data/'raw').is_dir():result['warnings'].append('data/raw missing: original evidence unavailable')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return bool(result['errors'])


if __name__=='__main__':sys.exit(main())
