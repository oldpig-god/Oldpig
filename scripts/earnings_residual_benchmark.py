"""One frozen seasonal-residual hypothesis plus five training-label controls."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.dataset import load_panel
from app.earnings.evaluation import EvaluationPolicy,paired_comparison
from app.earnings.experiments import counts,evaluate_registered
from app.earnings.models import TreeConfig,runtime_versions,shuffled_label_factory,tree_factory
from app.earnings.repository import Repository,LocalStore
from app.earnings.schema import digest
from app.earnings.sync import ManifestStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--reference',default='research/earnings/benchmark-summary-v2.json')
    parser.add_argument('--output',default='research/earnings/residual-summary.json')
    args=parser.parse_args()
    repo=Repository(LocalStore(args.data_dir))
    reference=json.loads(Path(args.reference).read_text(encoding='utf-8'))
    bundle=load_panel(repo,reference['sample_dataset_id'])
    frozen=ManifestStore(repo.store,reference['data_version'])
    universe=[frozen.get_json(k) for k in frozen.list_keys('earnings/universe/')]
    retired={r['entity_id'] for r in universe if r['field']=='delisted' and r['value']}
    policy=EvaluationPolicy(**reference['policy']);cfg=TreeConfig()
    model_config=dict(tree=asdict(cfg),libraries=runtime_versions(),target='scaled_residual_to_seasonal_naive')
    declarations=[dict(name='seasonal_residual',model_config=model_config)]
    for seed in (1729,1730,1731,1732,1733):
        declarations.append(dict(name='permuted_'+str(seed),model_config=dict(model_config,
            training_labels='permuted_within_temporal_fold',permutation_seed=seed)))
    suite=dict(data_version=reference['data_version'],dataset_id=bundle['id'],policy=asdict(policy),
               trials=declarations,hypothesis='Predict change from the same company prior-year quarter; unchanged features and tree parameters.')
    suite['id']=digest(suite)
    repo.append('earnings/experiments/suites/'+suite['id']+'.json.gz',suite)
    results=[]
    for trial in declarations:
        factory=tree_factory(cfg,seasonal_residual=True)
        if 'permutation_seed' in trial['model_config']:
            factory=shuffled_label_factory(factory,trial['model_config']['permutation_seed'])
        result=evaluate_registered(repo,bundle['rows'],data_version=reference['data_version'],
            evaluation_policy=policy,predictor=factory,model_config=trial['model_config'],
            retired_entities=retired,exclusions=bundle['exclusions'])
        results.append(result)
        print(json.dumps(dict(name=trial['name'],experiment_id=result['experiment_id']),ensure_ascii=False),flush=True)
    candidate='seasonal_residual_quantile_tree_financials'
    report=results[0]['report']
    old=next(t for t in reference['trials'] if t['name']=='tree_financials')
    prior=repo.store.get_json('earnings/experiments/results/'+old['experiment_id']+'.json.gz')['report']
    combined=report['predictions']+prior['predictions']
    summary=dict(suite_id=suite['id'],data_version=reference['data_version'],sample_dataset_id=bundle['id'],
        trials=[dict(name=t['name'],experiment_id=r['experiment_id'],metrics=r['report']['models'][candidate]['overall'])
                for t,r in zip(declarations,results)],
        against_seasonal=paired_comparison(report['predictions'],candidate,'seasonal_naive',policy),
        against_level_tree=paired_comparison(combined,candidate,'quantile_tree_financials',policy),
        benchmark_comparisons=report['benchmark_comparisons'],
        model_groups=report['models'][candidate],
        registry_counts=counts(repo),holdout_opened=policy.include_holdout)
    Path(args.output).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
