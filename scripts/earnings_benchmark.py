"""Run the declared baseline, quantile-tree ablation and five label-permutation controls.

All full results are immutable ledger objects. The exported JSON is a small review summary.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.earnings.dataset import build,load_panel,save_panel
from app.earnings.evaluation import EvaluationPolicy,paired_comparison
from app.earnings.experiments import counts,evaluate_registered
from app.earnings.models import TreeConfig,runtime_versions,shuffled_label_factory,tree_factory
from app.earnings.repository import LocalStore,Repository
from app.earnings.sync import ManifestStore


def main():
    parser=argparse.ArgumentParser(description='盈利预测固定实验套件')
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'data'))
    parser.add_argument('--cloud',action='store_true')
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--manifest');inputs.add_argument('--dataset')
    parser.add_argument('--start',default='2021-11-14');parser.add_argument('--end',default='2026-09-30')
    parser.add_argument('--warmup-start',default='2019-01-01');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.cloud:
        from app.cloud_store import CloudStore
        repo=Repository(CloudStore(upload_timeout=300))
    else:repo=Repository(LocalStore(args.data_dir))
    if args.dataset:
        bundle=load_panel(repo,args.dataset)
        args.manifest=bundle['data_version'];rows=bundle['rows'];exclusions=bundle['exclusions']
    else:
        frozen=Repository(ManifestStore(repo.store,args.manifest));exclusions=[]
        rows=build(frozen,start=args.warmup_start,end=args.end,exclusions=exclusions,output=repo)
        bundle=save_panel(repo,rows,args.manifest,exclusions)
    frozen=Repository(ManifestStore(repo.store,args.manifest))
    universe=[frozen.store.get_json(k) for k in frozen.store.list_keys('earnings/universe/')]
    retired={r['entity_id'] for r in universe if r['field']=='delisted' and r['value']}
    policy=EvaluationPolicy(evaluation_start=args.start)
    versions=runtime_versions()
    declared=[dict(name='baselines',model_config=dict(model='baselines'),predictor=None)]
    for feature_set in ('profit_history','financials'):
        cfg=TreeConfig(feature_set=feature_set)
        declared.append(dict(name='tree_'+feature_set,model_config=dict(tree=asdict(cfg),libraries=versions),predictor=tree_factory(cfg)))
    # These are negative controls, not configurations from which to select a winning model.
    for seed in (1729,1730,1731,1732,1733):
        cfg=TreeConfig()
        declared.append(dict(name='permuted_'+str(seed),
            model_config=dict(tree=asdict(cfg),libraries=versions,training_labels='permuted_within_temporal_fold',permutation_seed=seed),
            predictor=shuffled_label_factory(tree_factory(cfg),seed)))
    declaration=dict(data_version=args.manifest,policy=asdict(policy),
                     trials=[dict(name=t['name'],model_config=t['model_config']) for t in declared])
    from app.earnings.schema import digest
    declaration['id']=digest(declaration)
    repo.append('earnings/experiments/suites/'+declaration['id']+'.json.gz',declaration)
    results={}
    summary=dict(suite_id=declaration['id'],data_version=args.manifest,sample_dataset_id=bundle['id'],policy=asdict(policy),trials=[])
    for trial in declared:
        result=evaluate_registered(repo,rows,data_version=args.manifest,evaluation_policy=policy,
            predictor=trial['predictor'],model_config=trial['model_config'],retired_entities=retired,exclusions=exclusions)
        results[trial['name']]=result
        report=result['report']
        primary={k:v['overall'] for k,v in report['models'].items()
                 if trial['name']=='baselines' or k.startswith('quantile_tree_')}
        brief=dict(name=trial['name'],experiment_id=result['experiment_id'],models=primary,
                   benchmark_comparisons=report['benchmark_comparisons'])
        summary['trials'].append(brief)
        print(json.dumps(brief,ensure_ascii=False),flush=True)
    base=results['baselines']['report']
    summary['coverage']={k:base[k] for k in ('samples','warmup_samples','evaluated_samples','heldout_samples',
        'research_population','business_inapplicable','membership_unresolved',
        'abstained','abstention_rate','abstention_reasons','label_missing','label_missing_by_quarter','missing_membership','data_exclusions')}
    combined=results['tree_profit_history']['report']['predictions']+results['tree_financials']['report']['predictions']
    summary['financial_feature_ablation']=paired_comparison(combined,'quantile_tree_financials','quantile_tree_profit_history',policy)
    real=summary['trials'][2]['models'].get('quantile_tree_financials')
    controls=[t['models']['quantile_tree_financials']['scaled_mae'] for t in summary['trials'][3:]
              if 'quantile_tree_financials' in t['models']]
    summary['label_permutation']=dict(controls=controls,real_mae=real['scaled_mae'] if real else None,
        controls_at_least_as_good=sum(v<=real['scaled_mae'] for v in controls) if real else None,
        description='5组固定种子对照；不用于挑选模型参数，也不据此宣称统计显著')
    summary['registry_counts']=counts(repo)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(suite_id=summary['suite_id'],registry_counts=summary['registry_counts']),ensure_ascii=False))


if __name__=='__main__':
    import requests
    try:main()
    except requests.RequestException as error:
        sys.exit('网络请求失败：'+type(error).__name__)
