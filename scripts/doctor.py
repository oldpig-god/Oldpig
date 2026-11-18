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


def latest_screen(c,tables,warnings):
    """Summary of the newest screen_runs row (read-only; mirrors app.screener.passes)."""
    if 'screen_runs' not in tables:
        warnings.append('screen_runs table absent: start the server once or run python -m app.screener');return None
    row=c.execute('SELECT id,created_at,as_of,status,payload FROM screen_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    total=c.execute('SELECT count(*) FROM screen_runs').fetchone()[0]
    if not row:
        warnings.append('No screen run yet: click 运行筛选 or run python -m app.screener');return dict(runs=0)
    out=dict(runs=total,id=row[0],created_at=row[1],as_of=row[2],status=row[3])
    try:
        p=json.loads(row[4]);items=p.get('items') or []
        rules=['not_st','cap','pe_positive','pe_band','ma','ema','macd']
        def passed(checks,band):
            return all((checks.get(k) is not False) if k=='pe_band' else checks.get(k) is True
                       for k in rules if band or k!='pe_band')
        out.update(quote_fetched_at=p.get('quote_fetched_at'),criteria=p.get('criteria'),counts=p.get('counts'),
                   candidates=len(items),fetch_errors=(lambda v:v if isinstance(v,int) else len(v or []))(p.get('fetch_errors')),
                   passed_with_band=sum(passed(i.get('checks',{}),True) for i in items),
                   passed_without_band=sum(passed(i.get('checks',{}),False) for i in items),
                   stale_bars=sum(bool(i.get('stale')) for i in items),warnings=[str(w)[:200] for w in p.get('warnings') or []])
        if row[3]!='ok':warnings.append(f'Latest screen run status is {row[3]}; see fetch_errors in /api/screen')
    except Exception as exc:warnings.append('Screen payload unreadable: '+str(exc))
    return out


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--deep',action='store_true');parser.add_argument('--technical-only',action='store_true');args=parser.parse_args()
    result=dict(python=sys.version,executable=sys.executable,platform=platform.platform(),bits=struct.calcsize('P')*8,
                root=str(ROOT),dependencies={},errors=[],warnings=[])
    if sys.version_info < (3,10):result['errors'].append('Python 3.10+ required; recommend 3.11 x64')
    if result['bits']!=64:result['errors'].append('64-bit Python required for this large dataset')
    for package in ['akshare','fastapi','uvicorn','pandas','requests','tzdata','numpy']:
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
                missing={'instruments','batches','metadata','history_tasks'}-tables
                if 'bars' not in tables and 'observations' not in tables:missing.add('bars')
                if missing:result['errors'].append('Missing tables: '+str(missing))
                else:
                    result['active_instruments']=c.execute('SELECT count(*) FROM instruments WHERE active=1').fetchone()[0]
                    result['queue']=dict(c.execute('SELECT status,count(*) FROM history_tasks GROUP BY status'))
                result['latest_screen']=latest_screen(c,tables,result['warnings'])
                if args.deep:
                    integrity=[r[0] for r in c.execute('PRAGMA quick_check')];result['quick_check']=integrity
                    if integrity!=['ok']:result['errors'].append('SQLite quick_check failed')
        except Exception as exc:result['errors'].append('Database read error: '+str(exc))
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
