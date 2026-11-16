"""Same-time YTD guidance versus seasonal YTD baseline; separate from quarterly models."""
from collections import Counter
from datetime import date,datetime,timedelta
from decimal import Decimal
from .guidance import asof
from .pit import availability,select
from .schema import SH,stamp


def evaluate_guidance(events,records,start='2021-11-14',holdout='2026-06-30',offsets=(1,15,30)):
    periods=sorted({e['report_period'] for e in events if start<=e['report_period']<holdout})
    outcomes=[];excluded=Counter()
    for period in periods:
        # The first benchmark covers Q1, half-year and annual YTD releases. A title
        # saying "third quarter" alone does not distinguish Q3-only from nine months.
        if period.endswith('-09-30'):
            excluded['q3_period_basis_not_reviewed']+=len(offsets);continue
        targets=[e for e in events if e['report_period']==period and e['revision_kind']=='initial']
        if not targets:continue
        first=min(targets,key=availability)
        labels={e['facts'].get('core_profit',{}).get('value') for e in targets if availability(e)==availability(first)}
        if len(labels)!=1 or None in labels:
            excluded['first_core_label_missing_or_conflicting']+=len(offsets);continue
        for offset in offsets:
            at=datetime.combine(date.fromisoformat(period)+timedelta(days=offset),datetime.min.time(),SH).replace(hour=8,minute=30).isoformat()
            if availability(first)<=stamp(at):
                excluded['already_disclosed']+=1;continue
            known=select(events,at,issues={})
            previous=str(int(period[:4])-1)+period[4:]
            base=known.get(previous,{}).get('facts',{}).get('core_profit')
            if not base:
                excluded['seasonal_ytd_missing']+=1;continue
            guidance=asof(records,period,at)
            if not guidance:
                excluded['no_public_core_guidance']+=1;continue
            for kind,value in guidance.items():
                actual=Decimal(first['facts']['core_profit']['value']);baseline=Decimal(base['value'])
                lower=Decimal(value['lower']);upper=Decimal(value['upper']);point=(lower+upper)/2
                outcomes.append(dict(entity_id=first['entity_id'],symbol=first['symbol'],name=first['name'],
                    target_period=period,prediction_at=at,offset_days=offset,kind=kind,
                    actual=float(actual),baseline=float(baseline),point=float(point),lower=float(lower),upper=float(upper),
                    guidance_absolute_error=float(abs(point-actual)),baseline_absolute_error=float(abs(baseline-actual)),
                    disclosed_range_hit=lower<=actual<=upper,record_id=value['record_id'],
                    guidance_available_at=value['available_at'],label_available_at=first['available_at'],
                    label_event_id=first['id'],baseline_event_id=known[previous]['id']))
    return outcomes,dict(excluded)


def summarize(rows):
    groups=[]
    for offset,kind in sorted({(r['offset_days'],r['kind']) for r in rows}):
        sample=[r for r in rows if (r['offset_days'],r['kind'])==(offset,kind)];n=len(sample)
        groups.append(dict(offset_days=offset,kind=kind,n=n,companies=len({r['entity_id'] for r in sample}),
            guidance_amount_mae=sum(r['guidance_absolute_error'] for r in sample)/n,
            baseline_amount_mae=sum(r['baseline_absolute_error'] for r in sample)/n,
            disclosed_range_hit_rate=sum(r['disclosed_range_hit'] for r in sample)/n))
    return groups
