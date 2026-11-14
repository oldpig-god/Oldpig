"""Versioned feature registry and reproducible, source-traced as-of snapshots."""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from .schema import METRICS, SCHEMA, digest, period, stamp
from .pit import previous_period, quarter, select

@dataclass(frozen=True)
class Policy:
    max_financial_age_days: int = 550


def registry():
    result = {}
    for metric, (basis, label) in METRICS.items():
        forms = [basis] + (['quarter'] if basis=='ytd' else [])
        for form in forms:
            name = metric+'_'+form
            result[name] = dict(label=label, unit='CNY', frequency='fiscal_quarter',
                source='verified_public_financial_report', transform=form,
                missing_policy='null_with_reason', publication='max_parent_available_at', version=SCHEMA)
    result['core_profit_to_assets'] = dict(label='单季度扣非利润/已公开总资产',unit='ratio',
        frequency='fiscal_quarter',source='verified_public_financial_report',transform='divide',
        missing_policy='null_with_reason',publication='max_parent_available_at',version=SCHEMA)
    return result


def snapshot(events, entity_id, prediction_at, target_period, policy=Policy()):
    target = period(target_period)
    versions = select([e for e in events if e['entity_id']==entity_id], prediction_at)
    cutoff = stamp(prediction_at)
    inputs = sorted(e['id'] for e in versions.values())
    latest_period = max((p for p in versions if p < target_period), default=None)
    definitions = registry()
    features = {}
    def add(name, value, parents, reason=None):
        source_events = [e for e in versions.values() if e['id'] in parents]
        features[name] = dict(value=value, missing=value is None, reason=reason,
            parents=parents, available_at=max((e['available_at'] for e in source_events),default=None),
            **definitions[name])
    if latest_period:
        current = versions[latest_period]
        age = (cutoff.date()-date.fromisoformat(latest_period)).days
        for metric,(basis,_) in METRICS.items():
            fact = current['facts'].get(metric)
            add(metric+'_'+basis, fact['value'] if fact and age<=policy.max_financial_age_days else None,
                [current['id']], '财报过期' if age>policy.max_financial_age_days else None if fact else '字段缺失')
            if basis=='ytd':
                q=quarter(current,versions.get(previous_period(latest_period)),metric)
                add(metric+'_quarter',q['value'] if age<=policy.max_financial_age_days else None,q['parents'],
                    '财报过期' if age>policy.max_financial_age_days else q['reason'])
        asset=features['assets_instant'];profit=features['core_profit_quarter']
        if asset['value'] is not None and Decimal(asset['value'])>0 and profit['value'] is not None:
            value=format(Decimal(profit['value'])/Decimal(asset['value']),'.16f');reason=None
        else:value=None;reason='缺少单季度利润或正总资产'
        add('core_profit_to_assets',value,sorted(set(asset['parents']+profit['parents'])),reason)
    else:
        for name in definitions:add(name,None,[],'预测时点无已披露历史报告')
    mode='forecast' if cutoff.date()<=target else 'nowcast'
    result=dict(schema=SCHEMA,entity_id=entity_id,prediction_at=cutoff.isoformat(),target_period=target_period,
        horizon_mode=mode,status='target_disclosed' if target_period in versions else 'ready' if latest_period else 'no_history',
        latest_report_period=latest_period,features=features,input_event_ids=inputs,policy=asdict(policy),
        registry_hash=digest(definitions))
    result['id']=digest(result)
    return result
