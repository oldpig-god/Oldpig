"""Immutable experiment registration, output and failed-run receipts."""
from dataclasses import asdict
from pathlib import Path
import platform
import subprocess
from .dataset import SamplePolicy, build
from .evaluation import EvaluationPolicy, evaluate
from .schema import digest


def engine_version():
    return digest({p.name: p.read_text(encoding='utf-8')
                   for p in sorted(Path(__file__).parent.glob('*.py'))})


def run(repository, *, start, end, data_version, sample_policy=SamplePolicy(),
        evaluation_policy=EvaluationPolicy(), predictor=None, model_config=None):
    exclusions=[]
    rows = build(repository, start=start, end=end, policy=sample_policy,exclusions=exclusions)
    return evaluate_registered(repository, rows, data_version=data_version, evaluation_policy=evaluation_policy,
                               predictor=predictor, model_config=model_config,exclusions=exclusions)


def evaluate_registered(repository, rows, *, data_version, evaluation_policy=EvaluationPolicy(),
                        predictor=None, model_config=None, retired_entities=(),exclusions=()):
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[2],
                                text=True, check=True, capture_output=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = 'source_distribution'
    registration = dict(data_version=data_version, sample_ids=sorted(r['id'] for r in rows),
                        code_version=commit, engine_hash=engine_version(), python=platform.python_version(),
                        evaluation_policy=asdict(evaluation_policy), model_config=model_config or {'model': 'baselines'})
    policies={digest(r['policy']):r['policy'] for r in rows}
    registration['sample_policies']=[policies[k] for k in sorted(policies)]
    registration['data_exclusions']=list(exclusions)
    registration['id'] = digest(registration)
    experiment_id = registration['id']
    repository.append('earnings/experiments/registrations/'+experiment_id+'.json.gz', registration)
    try:
        report = evaluate(rows, evaluation_policy, predictor=predictor, retired_entities=retired_entities)
        report['data_exclusions']=list(exclusions)
        report['id']=digest({k:v for k,v in report.items() if k!='id'})
        result = dict(experiment_id=experiment_id, status='completed', report=report)
        repository.append('earnings/experiments/results/'+experiment_id+'.json.gz', result)
    except Exception as error:
        failure = dict(experiment_id=experiment_id, status='failed', error=type(error).__name__, message=str(error))
        failure['id'] = digest(failure)
        repository.append('earnings/experiments/failures/'+failure['id']+'.json.gz', failure)
        raise
    return result


def counts(repository):
    registrations = [repository.store.get_json(k) for k in repository.store.list_keys('earnings/experiments/registrations/')]
    return dict(experiments=len(registrations),
                configurations=len({digest({k:r[k] for k in ('model_config','evaluation_policy','sample_policies')})
                                    for r in registrations}),
                failures=len(repository.store.list_keys('earnings/experiments/failures/')))
