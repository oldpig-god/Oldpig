"""Dated company/alias, listing, industry and size facts share the same as-of clock."""
from .schema import DataError, available_time, stamp

FIELDS={'listed','delisted','suspension','industry','market_cap','symbol'}


def asof(records,at):
    cutoff=stamp(at);result={}
    for record in records:
        if record['field'] not in FIELDS:raise DataError('未登记证券池字段')
        if record['provenance'] not in ('reconstructed_pit','observed_pit'):continue
        public=available_time(record['published_at'],record['time_precision'])
        known=max(public,stamp(record['ingested_at'])) if record['provenance']=='observed_pit' else public
        if known>=cutoff or stamp(record['effective_at'])>cutoff:continue
        key=(record['entity_id'],record['field'])
        ordering=(stamp(record['effective_at']),known)
        if key not in result or ordering>result[key][0]:result[key]=(ordering,record)
    return {key:r for key,(_,r) in result.items()}


def eligible_entities(records,at):
    states=asof(records,at);entities={entity for entity,field in states if field=='listed'}
    return sorted(entity for entity in entities if states[(entity,'listed')]['value']
        and not states.get((entity,'delisted'),{}).get('value',False))
