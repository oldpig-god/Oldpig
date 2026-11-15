"""Fixed-schedule point-in-time samples; labels are kept outside model inputs."""
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from .pit import availability, select
from .quarters import history, load_cells, target
from .schema import METRICS, SH, DataError, digest, stamp
from .universe import asof, eligible_entities


@dataclass(frozen=True)
class SamplePolicy:
    min_history_quarters: int = 8
    max_financial_age_days: int = 550
    prediction_hour: int = 8
    prediction_minute: int = 30


def shift_period(value, quarters):
    d = date.fromisoformat(value)
    index = d.year*4+d.month//3-1+quarters
    year, quarter = divmod(index, 4)
    month = (quarter+1)*3
    return date(year, month, monthrange(year, month)[1]).isoformat()


def scheduled_at(report_period, policy=SamplePolicy()):
    day = date.fromisoformat(report_period)+timedelta(days=1)
    return datetime.combine(day, datetime.min.time(), SH).replace(
        hour=policy.prediction_hour, minute=policy.prediction_minute).isoformat()


def feature_registry():
    names=['target_quarter','latest_report_quarter','report_lag_quarters','history_count','recent_yoy_delta','mean_yoy_delta',
           'seasonal_naive','seasonal_delta','yoy_trend']
    names += ['core_lag_'+str(lag) for lag in (1,2,3,4,5,8)]
    names += [m+'_latest_to_scale' for m in METRICS]
    return {name:dict(source='public_report_pit' if name!='target_quarter' else 'fixed_schedule',
                      frequency='fiscal_quarter', unit='count' if name in ('target_quarter','latest_report_quarter','report_lag_quarters','history_count') else 'ratio',
                      publication='max_feature_evidence_available_at', missing_policy='null_no_future_fill')
            for name in names}


def sample(events, cells, entity_id, report_period, *, policy=SamplePolicy(), universe=()):
    at = scheduled_at(report_period, policy)
    versions = select(events, at)
    profits = {p: v for p, v in history(events, cells, at).items() if p < report_period}
    periods = sorted(profits)
    latest = max((p for p in versions if p < report_period), default=None)
    reasons = []
    if report_period in versions:
        reasons.append('目标已披露')
    if len(periods) < policy.min_history_quarters:
        reasons.append('历史单季样本不足')
    if not latest:
        reasons.append('无已公开财报')
    elif (stamp(at).date()-date.fromisoformat(latest)).days > policy.max_financial_age_days:
        reasons.append('财报过期')
    scale = None
    scale_method = None
    evidence = []
    if latest:
        event = versions[latest]
        asset = event['facts'].get('assets')
        if asset and Decimal(asset['value']) > 0:
            scale = float(asset['value'])
            scale_method = 'public_assets'
            evidence.append(dict(kind='scale', parents=[event['id']], available_at=event['available_at']))
        else:
            revenues = history(events, cells, at, metric='revenue')
            trailing = [revenues.get(shift_period(latest, -i)) for i in range(4)]
            if all(trailing):
                ttm = sum(float(v['value']) for v in trailing)
                if ttm > 0:
                    scale = ttm
                    scale_method = 'public_ttm_revenue'
                    evidence += [dict(v, kind='scale') for v in trailing]
    if scale is None:
        reasons.append('缺少正总资产及正TTM营收')
    known = lambda p: float(profits[p]['value']) if p in profits else None
    seasonal = known(shift_period(report_period, -4))
    if seasonal is None:
        reasons.append('缺少上年同季利润')
    if periods and (stamp(at).date()-date.fromisoformat(periods[-1])).days > policy.max_financial_age_days:
        reasons.append('单季利润过期')
    changes = []
    for p in reversed(periods):
        prior = shift_period(p, -4)
        if prior in profits:
            changes.append(known(p)-known(prior))
    baselines = {
        'seasonal_naive': seasonal,
        'seasonal_delta': seasonal+changes[0] if seasonal is not None and changes else None,
        'yoy_trend': seasonal+sum(changes[:3])/len(changes[:3]) if seasonal is not None and changes else None,
    }
    features = dict.fromkeys(feature_registry())
    features.update(target_quarter=int(report_period[5:7])//3,history_count=len(periods))
    if latest:
        features['latest_report_quarter']=int(latest[5:7])//3
        features['report_lag_quarters']=(int(report_period[:4])-int(latest[:4]))*4+(int(report_period[5:7])-int(latest[5:7]))//3
    for lag in (1, 2, 3, 4, 5, 8):
        value = known(shift_period(report_period, -lag))
        features['core_lag_'+str(lag)] = value/scale if value is not None and scale else None
    features['recent_yoy_delta'] = changes[0]/scale if changes and scale else None
    features['mean_yoy_delta'] = sum(changes[:3])/len(changes[:3])/scale if changes and scale else None
    for name, value in baselines.items():
        features[name] = value/scale if value is not None and scale else None
    if latest and scale:
        for metric in METRICS:
            fact = versions[latest]['facts'].get(metric)
            features[metric+'_latest_to_scale'] = float(fact['value'])/scale if fact else None
        evidence.append(dict(kind='latest_financials', parents=[versions[latest]['id']],
                             available_at=versions[latest]['available_at']))
    evidence += [dict(v, kind='historical_profit') for v in profits.values()]
    state = asof(universe, at)
    groups = {}
    for field in ('industry', 'market_cap'):
        record = state.get((entity_id, field))
        groups[field] = record['value'] if record else 'unknown'
    recent = [known(p) for p in periods[-4:]]
    groups['profit_type'] = 'loss' if recent and recent[-1] < 0 else 'unclassified'
    groups['year'] = report_period[:4]
    groups['target_quarter'] = str(int(report_period[5:7])//3)
    groups['scale_method'] = scale_method or 'missing'
    retired=state.get((entity_id,'delisted'))
    listed=state.get((entity_id,'listed'))
    if retired and retired['value']:
        reasons.append('预测时点已退市');membership='verified_delisted'
    elif listed and not listed['value']:
        reasons.append('预测时点尚未上市');membership='verified_not_listed'
    else:
        membership='verified' if listed else 'report_identity' if versions else 'unverified'
    label = target(events, cells, report_period)
    result = dict(entity_id=entity_id, target_period=report_period, prediction_at=at,
                  horizon_mode='nowcast', status='abstain' if reasons else 'ready', reasons=reasons,
                  policy=asdict(policy), scale=scale, scale_method=scale_method,
                  features=features, feature_registry_hash=digest(feature_registry()),
                  feature_evidence=evidence, baselines=baselines,
                  year_ago=seasonal, label=label, groups=groups, membership=membership)
    audit(result)
    result['id'] = digest(result)
    return result


def audit(row):
    cutoff = stamp(row['prediction_at'])
    for evidence in row['feature_evidence']:
        if stamp(evidence['available_at']) >= cutoff:
            raise ValueError('发现预测时点之后的特征')
    label = row['label']
    if label.get('value') is not None and stamp(label['label_available_at']) <= cutoff:
        raise ValueError('目标已公开，不能计为预测')
    if 'id' in row and row['id']!=digest({k:v for k,v in row.items() if k!='id'}):
        raise ValueError('样本内容与版本标识不一致')


def build(repository, *, start, end, policy=SamplePolicy(),exclusions=None,output=None):
    writer=output or repository
    universe = [repository.store.get_json(k) for k in repository.store.list_keys('earnings/universe/')]
    entities = sorted({k.split('/')[2] for k in repository.store.list_keys('earnings/events/')} |
                      {r['entity_id'] for r in universe if r['field']=='listed'})
    definitions=feature_registry()
    writer.append('earnings/registry/'+digest(definitions)+'.json.gz',definitions)
    rows = []
    for entity in entities:
        events = repository.events(entity)
        cells = load_cells(repository, entity)
        periods=[]
        p=start[:4]+'-03-31'
        while p<=end:
            if p>=start:periods.append(p)
            p=shift_period(p,1)
        try:
            company_rows=[sample(events,cells,entity,p,policy=policy,universe=universe) for p in periods]
        except DataError as error:
            record=dict(entity_id=entity,start=start,end=end,reason=str(error),
                        event_ids=sorted(e['id'] for e in events),policy=asdict(policy))
            record['id']=digest(record)
            writer.append('earnings/exclusions/'+record['id']+'.json.gz',record)
            if exclusions is not None:exclusions.append(record)
            continue
        for row in company_rows:
            writer.append('earnings/samples/'+entity+'/'+row['id']+'.json.gz', row)
            rows.append(row)
    return rows


def save_panel(repository,rows,data_version,exclusions=()):
    """One immutable, compressed panel avoids thousands of cloud requests on replay."""
    panel=dict(data_version=data_version,rows=rows,exclusions=list(exclusions),
               feature_registry=feature_registry())
    panel['id']=digest(panel)
    repository.append('earnings/datasets/'+panel['id']+'.json.gz',panel)
    return panel


def load_panel(repository,panel_id):
    panel=repository.store.get_json('earnings/datasets/'+panel_id+'.json.gz')
    if digest({k:v for k,v in panel.items() if k!='id'})!=panel_id:
        raise ValueError('样本面板内容与版本标识不一致')
    for row in panel['rows']:audit(row)
    return panel
