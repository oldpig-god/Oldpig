"""Recompute one frozen financial model and compare its complete saved report."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.earnings.dataset import load_panel
from app.earnings.evaluation import EvaluationPolicy, evaluate
from app.earnings.models import TreeConfig, tree_factory
from app.earnings.repository import LocalStore, Repository
from app.earnings.schema import digest
from app.earnings.sync import ManifestStore


def main():
    parser = argparse.ArgumentParser(description='核对专项包的完整模型报告')
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--summary', default='benchmark-summary.json')
    parser.add_argument('--output', default='replay-check.json')
    args = parser.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding='utf-8'))
    repo = Repository(LocalStore(args.data_dir))
    bundle = load_panel(repo, summary['sample_dataset_id'])
    trial = next(t for t in summary['trials'] if t['name'] == 'tree_financials')
    prefix = 'earnings/experiments/'
    registration = repo.store.get_json(prefix+'registrations/'+trial['experiment_id']+'.json.gz')
    saved = repo.store.get_json(prefix+'results/'+trial['experiment_id']+'.json.gz')['report']
    frozen = ManifestStore(repo.store, summary['data_version'])
    universe = [frozen.get_json(k) for k in frozen.list_keys('earnings/universe/')]
    retired = {r['entity_id'] for r in universe if r['field'] == 'delisted' and r['value']}
    report = evaluate(bundle['rows'], EvaluationPolicy(**registration['evaluation_policy']),
                      predictor=tree_factory(TreeConfig(**registration['model_config']['tree'])),
                      retired_entities=retired)
    report['data_exclusions'] = bundle['exclusions']
    report['id'] = digest({k: v for k, v in report.items() if k != 'id'})
    result = dict(data_version=summary['data_version'], dataset_id=bundle['id'],
                  expected_report_id=saved['id'], actual_report_id=report['id'],
                  exact_report_match=report == saved)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['exact_report_match'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
