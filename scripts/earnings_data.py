"""Original-report ledger: collect and query without changing the financial UI cache.

Windows: python scripts/earnings_data.py --help
Cloud writes use the existing CloudStore environment variables; no credentials in arguments.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.earnings.repository import LocalStore, Repository
from app.earnings.service import collect, materialize


def main():
    parser=argparse.ArgumentParser(description='盈利预测 PIT 数据层')
    parser.add_argument('--cloud',action='store_true')
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'data'))
    sub=parser.add_subparsers(dest='action',required=True)
    command=sub.add_parser('collect');command.add_argument('--symbols',nargs='+',required=True)
    command.add_argument('--start',required=True);command.add_argument('--end',required=True)
    command.add_argument('--max-reports',type=int,default=24);command.add_argument('--reviews')
    push=sub.add_parser('push');push.add_argument('--source-dir',required=True)
    query=sub.add_parser('snapshot');query.add_argument('--entity',required=True)
    query.add_argument('--at',required=True);query.add_argument('--target',required=True);query.add_argument('--manifest')
    args=parser.parse_args()
    if args.cloud:
        from app.cloud_store import CloudStore
        store=CloudStore()
    else:store=LocalStore(args.data_dir)
    repository=Repository(store)
    if args.action=='collect':
        reviews=json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else {}
        results=[]
        from app.earnings.cninfo import Source
        source=Source()
        for symbol in args.symbols:
            result=collect(repository,symbol,args.start,args.end,source=source,reviews=reviews,max_reports=args.max_reports)
            results.append(result)
            print(json.dumps(dict(symbol=symbol,processed=result['processed'],remaining=result['remaining'],
                statuses={s:sum(r['status']==s for r in result['outcomes']) for s in {r['status'] for r in result['outcomes']}}),ensure_ascii=False),flush=True)
        path=Path(args.data_dir)/'earnings-collection-result.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    elif args.action=='snapshot':
        if args.manifest:
            from app.earnings.sync import ManifestStore
            from app.earnings.features import snapshot
            frozen=Repository(ManifestStore(store,args.manifest))
            result=snapshot(frozen.events(args.entity),args.entity,args.at,args.target)
        else:result=materialize(repository,args.entity,args.at,args.target)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        from app.earnings.sync import publish
        print(json.dumps(publish(Repository(LocalStore(args.source_dir)),repository),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
