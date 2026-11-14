"""As-of version selection and cumulative accounting transformations."""
from datetime import date
from decimal import Decimal
from .schema import DataError, available_time, period, stamp

ADMITTED = ('reconstructed_pit', 'observed_pit')


def availability(event):
    published=available_time(event['version_published_at'],event['time_precision'])
    effective=max(published,stamp(event['ingested_at'])) if event['provenance']=='observed_pit' else published
    if stamp(event['available_at'])!=effective:raise DataError('可用时间与公开证据不一致')
    return effective


def select(events, at):
    """Strictly earlier than t. Conflicting independent versions require a revision link."""
    cutoff = stamp(at)
    visible = [e for e in events if e['provenance'] in ADMITTED and availability(e) < cutoff]
    by_period = {}
    for e in visible:
        by_period.setdefault(e['report_period'], []).append(e)
    result = {}
    for p, values in by_period.items():
        ids = {e['id'] for e in values}
        for e in values:
            if e['supersedes'] and e['supersedes'] not in ids:raise DataError('修订链前序版本缺失')
        superseded = {e['supersedes'] for e in values if e['supersedes'] in ids}
        tips = [e for e in values if e['id'] not in superseded]
        if len(tips) != 1:
            raise DataError(p+' 存在未关联的报告版本')
        result[p] = tips[0]
    return result


def previous_period(value):
    p = period(value)
    if p.month == 3:
        return f'{p.year-1}-12-31'
    return f'{p.year}-{ {6:"03-31",9:"06-30",12:"09-30"}[p.month]}'


def quarter(current, previous, metric):
    """Return a value and its lineage; missing predecessors remain missing."""
    fact = current['facts'].get(metric)
    empty = dict(value=None, parents=[current['id']], reason='字段缺失')
    if fact is None:
        return empty
    if fact['basis'] != 'ytd':
        raise DataError('期末存量不能做累计差分')
    if period(current['report_period']).month == 3:
        return dict(value=fact['value'], parents=[current['id']], reason=None)
    if previous is None or previous['report_period'] != previous_period(current['report_period']):
        return dict(empty, reason='缺少同年相邻累计报告')
    if any(current[k] != previous[k] for k in ('entity_id','currency','accounting_scope','scope_version')):
        return dict(empty, reason='合并范围或会计口径不可比')
    if previous['revision_kind']!='initial' and availability(previous)>availability(current):
        return dict(empty,reason='前序已更正，当前累计口径需要复核')
    before = previous['facts'].get(metric)
    if before is None:
        return dict(empty, parents=[current['id'],previous['id']], reason='前序累计字段缺失')
    return dict(value=format(Decimal(fact['value'])-Decimal(before['value']), 'f'),
        parents=[current['id'],previous['id']], reason=None)


def first_disclosed_target(events, report_period, metric='core_profit'):
    """Freeze the target using versions visible at its first formal disclosure, not today."""
    candidates = [e for e in events if e['report_period'] == report_period
        and e['provenance'] in ADMITTED and e['revision_kind']=='initial']
    if not candidates:
        return dict(value=None, parents=[], reason='缺少首次正式披露')
    first = min(candidates, key=lambda e:availability(e))
    # At this instant the target is known, while features at t remain strictly earlier.
    eligible = [e for e in events if availability(e) <= stamp(first['available_at'])]
    from datetime import timedelta
    versions = select(eligible, (stamp(first['available_at'])+timedelta(microseconds=1)).isoformat())
    return dict(quarter(first, versions.get(previous_period(report_period)), metric),
        label_available_at=first['available_at'], report_period=report_period)
