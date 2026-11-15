"""Expanding-quarter evaluation and past-only residual interval calibration."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from math import ceil
from statistics import mean
import random
from .dataset import audit,shift_period
from .schema import digest, stamp

BASELINES = ('seasonal_naive', 'seasonal_delta', 'yoy_trend')


@dataclass(frozen=True)
class EvaluationPolicy:
    evaluation_start: str = '2021-11-14'
    alpha: float = .2
    min_calibration_samples: int = 40
    min_calibration_periods: int = 4
    calibration_window_quarters: int = 8
    min_training_samples: int = 80
    min_training_periods: int = 8
    bootstrap_repeats: int = 1000
    bootstrap_block_quarters: int = 4
    min_comparison_periods: int = 8
    seed: int = 1729
    holdout_start: str = '2026-06-30'
    include_holdout: bool = False


def interval(point, past, policy, *, raw_lower=None, raw_upper=None):
    periods = sorted({r['target_period'] for r in past})[-policy.calibration_window_quarters:]
    pool = [r for r in past if r['target_period'] in periods]
    if len(pool) < policy.min_calibration_samples or len(periods) < policy.min_calibration_periods:
        return dict(lower=None, upper=None, calibration_n=len(pool), calibration_periods=len(periods),
                    interval_status='calibration_sample_insufficient')
    scores = sorted(max(r['raw_lower']-r['actual_scaled'],r['actual_scaled']-r['raw_upper'],0)
                    if raw_lower is not None else abs(r['actual_scaled']-r['point_scaled']) for r in pool)
    rank = ceil((len(scores)+1)*(1-policy.alpha))
    if rank > len(scores):
        return dict(lower=None, upper=None, calibration_n=len(pool), calibration_periods=len(periods),
                    interval_status='calibration_rank_unavailable')
    radius = scores[rank-1]
    return dict(lower=(raw_lower if raw_lower is not None else point)-radius,
                upper=(raw_upper if raw_upper is not None else point)+radius, calibration_n=len(pool),
                calibration_periods=len(periods), interval_status='calibrated')


def metrics(records, alpha=.2):
    if not records:
        return dict(n=0, scaled_mae=None, amount_mae=None, direction_accuracy=None,
                    direction_n=0, coverage_80=None, interval_n=0, mean_interval_width=None,
                    interval_score=None, pinball_10=None, pinball_50=None, pinball_90=None)
    directional = [r for r in records if r['year_ago'] is not None]
    calibrated = [r for r in records if r['lower'] is not None]
    def pinball(q, value, actual):
        error = actual-value
        return max(q*error, (q-1)*error)
    scores = [(r['upper']-r['lower']) + 2/alpha*max(r['lower']-r['actual_scaled'], 0)
              + 2/alpha*max(r['actual_scaled']-r['upper'], 0) for r in calibrated]
    return dict(n=len(records), scaled_mae=mean(abs(r['actual_scaled']-r['point_scaled']) for r in records),
                amount_mae=mean(abs(r['actual']-r['point']) for r in records),
                direction_accuracy=mean((r['point']>r['year_ago']) == (r['actual']>r['year_ago'])
                                        for r in directional) if directional else None,
                direction_n=len(directional),
                coverage_80=mean(r['lower'] <= r['actual_scaled'] <= r['upper'] for r in calibrated) if calibrated else None,
                interval_n=len(calibrated),
                mean_interval_width=mean(r['upper']-r['lower'] for r in calibrated) if calibrated else None,
                interval_score=mean(scores) if scores else None,
                pinball_10=mean(pinball(.1, r['lower'], r['actual_scaled']) for r in calibrated) if calibrated else None,
                pinball_50=mean(pinball(.5, r['point_scaled'], r['actual_scaled']) for r in records),
                pinball_90=mean(pinball(.9, r['upper'], r['actual_scaled']) for r in calibrated) if calibrated else None)


def grouped(records, field, alpha=.2):
    groups = defaultdict(list)
    for row in records:
        groups[str(row['groups'][field])].append(row)
    return {key: metrics(values, alpha) for key, values in sorted(groups.items())}


def paired_comparison(records, candidate, reference, policy):
    left = {r['sample_id']: r for r in records if r['model'] == candidate}
    right = {r['sample_id']: r for r in records if r['model'] == reference}
    pairs = [(left[key], right[key]) for key in sorted(left.keys() & right.keys())]
    blocks = defaultdict(list)
    for a, b in pairs:
        blocks[a['target_period']].append((a['entity_id'],abs(a['actual_scaled']-a['point_scaled'])-
                                          abs(b['actual_scaled']-b['point_scaled'])))
    delta = mean(v for values in blocks.values() for _,v in values) if blocks else None
    if len(blocks) < policy.min_comparison_periods:
        return dict(candidate=candidate, reference=reference, n=len(pairs), quarters=len(blocks),
                    candidate_metrics=metrics([a for a,_ in pairs],policy.alpha),
                    reference_metrics=metrics([b for _,b in pairs],policy.alpha),
                    scaled_mae_difference=delta, interval_95=None,
                    conclusion=f'时间折不足{policy.min_comparison_periods}，未判定显著提升')
    rng = random.Random(policy.seed)
    quarters = [min(blocks)]
    while quarters[-1]<max(blocks):quarters.append(shift_period(quarters[-1],1))
    companies=sorted({entity for values in blocks.values() for entity,_ in values})
    values = []
    # Resample company histories and contiguous year-long quarter blocks together.
    # This retains common-quarter shocks and within-company serial dependence.
    for _ in range(policy.bootstrap_repeats):
        selected=[]
        while len(selected)<len(quarters):
            start=rng.randrange(len(quarters))
            selected.extend(quarters[(start+j)%len(quarters)] for j in range(policy.bootstrap_block_quarters))
        time_weights=Counter(selected[:len(quarters)])
        company_weights=Counter(rng.choices(companies,k=len(companies)))
        numerator=0;denominator=0
        for p,weight in time_weights.items():
            for entity,value in blocks.get(p,()):
                w=weight*company_weights[entity]
                numerator+=w*value;denominator+=w
        if denominator:values.append(numerator/denominator)
    values.sort()
    lower, upper = values[int(.025*len(values))], values[min(len(values)-1, int(.975*len(values)))]
    return dict(candidate=candidate, reference=reference, n=len(pairs), quarters=len(blocks),
                candidate_metrics=metrics([a for a,_ in pairs],policy.alpha),
                reference_metrics=metrics([b for _,b in pairs],policy.alpha),
                companies=len(companies),bootstrap_block_quarters=policy.bootstrap_block_quarters,
                scaled_mae_difference=delta, interval_95=[lower, upper],
                conclusion='样本外误差降低' if upper < 0 else '未打败基准')


def evaluate(rows, policy=EvaluationPolicy(), *, predictor=None, retired_entities=()):
    """A predictor sees only earlier-quarter labels public before the fold's fit time."""
    for row in rows:
        audit(row)
    rows = sorted(rows, key=lambda r: (r['target_period'], r['entity_id']))
    keys = [(r['entity_id'], r['target_period'], r['prediction_at']) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('重复公司/报告期/预测时点样本')
    warmup=[r for r in rows if r['target_period']<policy.evaluation_start]
    held_out = [r for r in rows if r['target_period'] >= policy.holdout_start and not policy.include_holdout]
    candidates = [r for r in rows if r not in held_out and r not in warmup]
    forecasts = []
    folds = []
    retired = set(retired_entities)
    for p in sorted({r['target_period'] for r in candidates}):
        test = [r for r in candidates if r['target_period'] == p]
        fit_at = min(r['prediction_at'] for r in test)
        train = [r for r in rows if r['target_period'] < p and r['status'] == 'ready'
                 and r['label'].get('value') is not None
                 and stamp(r['label']['label_available_at']) < stamp(fit_at)]
        train_periods = sorted({r['target_period'] for r in train})
        fitted = None
        if predictor and len(train) >= policy.min_training_samples and len(train_periods) >= policy.min_training_periods:
            fitted = predictor(train)
        past = [r for r in forecasts if r['target_period'] < p
                and stamp(r['label_available_at']) < stamp(fit_at)]
        historical_scores = {m: metrics([r for r in past if r['model'] == m])['scaled_mae'] for m in BASELINES}
        available = [m for m in BASELINES if historical_scores[m] is not None]
        chosen = min(available, key=lambda m: historical_scores[m]) if available else 'seasonal_naive'
        folds.append(dict(target_period=p, fit_at=fit_at, train_n=len(train), train_periods=train_periods,
                          train_sample_ids=[r['id'] for r in train], test_n=len(test),
                          selected_baseline=chosen, learned_model_fitted=fitted is not None))
        for row in test:
            if row['status'] != 'ready' or row['label'].get('value') is None:
                continue
            point_values = {m: v for m, v in row['baselines'].items() if v is not None}
            if chosen in point_values:
                point_values['selected_baseline'] = point_values[chosen]
            if fitted:
                point_values.update(fitted(row))
            for model in sorted(point_values):
                point=point_values[model]
                bounds={}
                if isinstance(point,dict):
                    bounds={k:point[k] for k in ('raw_lower','raw_upper')}
                    point=point['point']
                point_scaled = point/row['scale']
                bands = interval(point_scaled, [r for r in past if r['model'] == model
                                               and r['scale_method']==row['scale_method']], policy,**bounds)
                record = dict(sample_id=row['id'], entity_id=row['entity_id'], target_period=p,
                              prediction_at=row['prediction_at'], model=model, point=point,
                              point_scaled=point_scaled, actual=float(row['label']['value']),
                              actual_scaled=float(row['label']['value'])/row['scale'], scale=row['scale'],
                              scale_method=row['scale_method'],horizon_mode=row['horizon_mode'],
                              label_available_at=row['label']['label_available_at'], year_ago=row['year_ago'],
                              groups=dict(row['groups'], retired='retired' if row['entity_id'] in retired else 'not_verified_retired'),
                              **bounds,**bands)
                forecasts.append(record)
    models = sorted({r['model'] for r in forecasts})
    summary = {}
    for model in models:
        items = [r for r in forecasts if r['model'] == model]
        summary[model] = dict(overall=metrics(items, policy.alpha),
                              by_group={field: grouped(items, field, policy.alpha) for field in
                                        ('year', 'target_quarter', 'scale_method', 'industry', 'market_cap', 'profit_type', 'retired')},
                              excluding_retired=metrics([r for r in items if r['entity_id'] not in retired], policy.alpha))
    inapplicable=[r for r in candidates if r['membership'] in ('verified_delisted','verified_not_listed')]
    unresolved=[r for r in candidates if r['membership']=='unverified']
    population=[r for r in candidates if r not in inapplicable and r not in unresolved]
    reason_counts = Counter(reason for r in population for reason in r['reasons'])
    ready = [r for r in population if r['status'] == 'ready']
    result = dict(policy=asdict(policy), samples=len(rows), evaluated_samples=len(candidates),
                  warmup_samples=len(warmup),
                  heldout_samples=len(held_out),research_population=len(population),
                  business_inapplicable=len(inapplicable),membership_unresolved=len(unresolved),
                  abstained=len(population)-len(ready),
                  abstention_rate=(len(population)-len(ready))/len(population) if population else None,
                  abstention_reasons=dict(reason_counts),
                  label_missing=sum(r['label'].get('value') is None for r in population),
                  label_missing_by_quarter=dict(Counter(r['groups']['target_quarter'] for r in population
                                                        if r['label'].get('value') is None)),
                  missing_membership=sum(r['membership'] == 'unverified' for r in candidates),
                  external_baselines={'earnings_guidance': 'not_connected', 'earnings_express': 'not_connected',
                                      'analyst_consensus': 'not_connected'},
                  folds=folds, models=summary, predictions=forecasts,
                  comparisons=[paired_comparison(forecasts, m, 'selected_baseline', policy)
                               for m in models if m != 'selected_baseline'],
                  benchmark_comparisons=[paired_comparison(forecasts,m,b,policy)
                                        for m in models if m not in (*BASELINES,'selected_baseline')
                                        for b in BASELINES])
    result['id'] = digest(result)
    return result
