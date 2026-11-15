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
    command=sub.add_parser('collect')
    symbols=command.add_mutually_exclusive_group(required=True)
    symbols.add_argument('--symbols',nargs='+');symbols.add_argument('--symbols-file')
    command.add_argument('--start',required=True);command.add_argument('--end',required=True)
    command.add_argument('--max-reports',type=int,default=24);command.add_argument('--reviews')
    panel=sub.add_parser('panel',help='断点续采已声明的公司研究面板')
    panel.add_argument('--symbols-file',required=True);panel.add_argument('--start',required=True)
    panel.add_argument('--end',required=True);panel.add_argument('--reviews');panel.add_argument('--max-reports',type=int,default=40)
    panel.add_argument('--output',required=True)
    push=sub.add_parser('push');push.add_argument('--source-dir',required=True)
    push.add_argument('--manifest')
    freeze=sub.add_parser('freeze',help='冻结数据清单，供实验及云端同步使用')
    freeze.add_argument('--financial-inputs',action='store_true',help='财务输入与原文引用；二进制原文独立归档')
    query=sub.add_parser('snapshot');query.add_argument('--entity',required=True)
    query.add_argument('--at',required=True);query.add_argument('--target',required=True);query.add_argument('--manifest')
    sub.add_parser('quarters',help='从已有原始公告提取明确披露的单季值')
    refresh=sub.add_parser('refresh',help='重新提取已有原文并记录字段修正及重复披露处理')
    refresh.add_argument('--reviews')
    refresh.add_argument('--entities',nargs='+')
    evaluate=sub.add_parser('evaluate',help='固定日期回放与基准滚动验证')
    evaluate.add_argument('--start',required=True);evaluate.add_argument('--end',required=True)
    evaluate.add_argument('--manifest',required=True);evaluate.add_argument('--output',required=True)
    evaluate.add_argument('--holdout-start',default='2026-06-30')
    evaluate.add_argument('--warmup-start',default='2019-01-01')
    evaluate.add_argument('--model',choices=['baselines','tree-history','tree-financials'],default='baselines')
    args=parser.parse_args()
    if args.cloud:
        from app.cloud_store import CloudStore
        store=CloudStore(upload_timeout=300)
    else:store=LocalStore(args.data_dir)
    repository=Repository(store)
    if args.action=='collect':
        reviews=json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else {}
        results=[]
        from app.earnings.cninfo import Source
        source=Source()
        symbol_list=json.loads(Path(args.symbols_file).read_text(encoding='utf-8')) if args.symbols_file else args.symbols
        for symbol in symbol_list:
            result=collect(repository,symbol,args.start,args.end,source=source,reviews=reviews,max_reports=args.max_reports)
            results.append(result)
            print(json.dumps(dict(symbol=symbol,processed=result['processed'],remaining=result['remaining'],
                statuses={s:sum(r['status']==s for r in result['outcomes']) for s in {r['status'] for r in result['outcomes']}}),ensure_ascii=False),flush=True)
        path=Path(args.data_dir)/'earnings-collection-result.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    elif args.action=='freeze':
        from app.earnings.sync import freeze
        print(json.dumps(freeze(repository,financial_inputs=args.financial_inputs)))
    elif args.action=='panel':
        from app.earnings.panel import collect_panel
        reviews=json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else {}
        result=collect_panel(repository,json.loads(Path(args.symbols_file).read_text(encoding='utf-8')),
            args.start,args.end,reviews=reviews,max_reports=args.max_reports,
            progress=lambda row:print(json.dumps(row,ensure_ascii=False),flush=True))
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    elif args.action=='snapshot':
        if args.manifest:
            from app.earnings.sync import ManifestStore
            from app.earnings.features import snapshot
            frozen=Repository(ManifestStore(store,args.manifest))
            result=snapshot(frozen.events(args.entity),args.entity,args.at,args.target)
        else:result=materialize(repository,args.entity,args.at,args.target)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif args.action=='refresh':
        from app.earnings.refresh import refresh,relink_archived_corrections
        from app.earnings.universe import seed_report_membership
        reviews=json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else {}
        result=refresh(repository,entities=args.entities,reviews=reviews)
        corrections=relink_archived_corrections(repository,reviews=reviews)
        seed_report_membership(repository)
        print(json.dumps(dict(companies=result,corrections=corrections),ensure_ascii=False,indent=2))
    elif args.action=='quarters':
        from app.earnings.pdf_extract import pages_from_pdf
        from app.earnings.quarters import save_cells
        entities=sorted({k.split('/')[2] for k in store.list_keys('earnings/events/')})
        for entity in entities:
            count=0
            for event in repository.events(entity):
                document=store.get_json('earnings/documents/'+entity+'/'+event['document_id']+'.json.gz')
                count+=bool(save_cells(repository,event,pages_from_pdf(repository.content(document))))
            print(json.dumps(dict(entity_id=entity,reports_with_direct_quarters=count)),flush=True)
    elif args.action=='evaluate':
        from app.earnings.sync import ManifestStore
        from app.earnings.dataset import build
        from app.earnings.evaluation import EvaluationPolicy
        from app.earnings.experiments import evaluate_registered,counts
        # Inputs are frozen; new sample/experiment receipts are appended to the live store.
        frozen=Repository(ManifestStore(store,args.manifest))
        exclusions=[]
        rows=build(frozen,start=min(args.start,args.warmup_start),end=args.end,exclusions=exclusions,output=repository)
        universe=[frozen.store.get_json(k) for k in frozen.store.list_keys('earnings/universe/')]
        retired={r['entity_id'] for r in universe if r['field']=='delisted' and r['value']}
        predictor=None;model_config=None
        if args.model!='baselines':
            from dataclasses import asdict
            from app.earnings.models import TreeConfig,runtime_versions,tree_factory
            config=TreeConfig(feature_set='profit_history' if args.model=='tree-history' else 'financials')
            predictor=tree_factory(config)
            model_config=dict(tree=asdict(config),libraries=runtime_versions())
        result=evaluate_registered(repository,rows,data_version=args.manifest,
            evaluation_policy=EvaluationPolicy(evaluation_start=args.start,holdout_start=args.holdout_start),retired_entities=retired,
            predictor=predictor,model_config=model_config,exclusions=exclusions)
        result['registry_counts']=counts(repository)
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(experiment_id=result['experiment_id'],samples=result['report']['samples'],
                             models={k:v['overall'] for k,v in result['report']['models'].items()},
                             registry_counts=result['registry_counts']),ensure_ascii=False,indent=2))
    else:
        from app.earnings.sync import publish
        scope=store.base+'/'+store.bucket if args.cloud else str(Path(args.data_dir).resolve())
        print(json.dumps(publish(Repository(LocalStore(args.source_dir)),repository,manifest_id=args.manifest,
                                 checkpoint_scope=scope),ensure_ascii=False,indent=2))

if __name__=='__main__':
    import requests
    try:main()
    except requests.RequestException as error:
        sys.exit('网络请求失败：'+type(error).__name__)
