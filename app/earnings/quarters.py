"""Single-quarter cells explicitly printed in original reports, with publication lineage."""
from .pdf_extract import cells, compact, proof, unit_in
from .pit import availability, first_disclosed_target, previous_period, quarter, select
from .schema import SCHEMA, digest, money, stamp

PARSER = 'direct-quarter-v1'


def extract_quarters(pages, report_period, cumulative_facts):
    """Q3 summary prints the current quarter; annual tables print all four quarters.

    Annual Q1–Q3 cells become historical inputs only on the annual publication date.
    They do not replace the first-disclosure targets of earlier quarterly reports.
    """
    result = {}
    core = cumulative_facts.get('core_profit')
    if report_period.endswith('-09-30') and core:
        p = core['proof']
        values = cells(p['quote'])
        if len(values) in (4, 8) and p['column'] == 'current_ytd':
            result[report_period] = {'core_profit': dict(p, raw_value=values[0], column='current_quarter')}
    if not report_period.endswith('-12-31'):
        return result
    active = False
    unit = None
    labels = {
        'core_profit': lambda s: '扣除非经常' in s or '经常性损益的净利润' in s,
        'parent_profit': lambda s: '归属于上市公司股东的净利润' in s and '扣除' not in s,
        'revenue': lambda s: s.startswith('营业收入'),
        'operating_cashflow': lambda s: '经营活动产生的现金流量净额' in s,
    }
    for index, text in enumerate(pages[:20]):
        lines = text.splitlines()
        for n, line in enumerate(lines):
            s = compact(line)
            if '分季度' in s and ('财务指标' in s or '财务数据' in s):
                active = True
            if active and '非经常性损益项目' in s:
                active = False
            if not active:
                continue
            unit = unit_in(line) or unit
            if not unit:
                continue
            for metric, matches in labels.items():
                if not matches(s):
                    continue
                candidates = [(j, v) for j, v in enumerate(lines[n:min(n+3, len(lines))], n)
                              if len(cells(v)) == 4]
                if not candidates:
                    continue
                j, row = candidates[0]
                values = cells(row)
                for suffix, raw in zip(('03-31', '06-30', '09-30', '12-31'), values):
                    result.setdefault(report_period[:4]+'-'+suffix, {})[metric] = dict(
                        proof(raw, unit, index+1, row, 'quarter_'+suffix),
                        label_page=index+1, label_quote=line)
    return result


def save_cells(repository, event, pages):
    values = extract_quarters(pages, event['report_period'], event['facts'])
    if not values:
        return None
    for facts in values.values():
        for metric, p in list(facts.items()):
            if p['quote'] not in pages[p['page']-1] or p['raw_value'] not in p['quote']:
                raise ValueError('单季单元格未匹配原文')
            facts[metric] = dict(value=money(p['raw_value'], p['unit']), basis='quarter', unit='CNY', proof=p)
    record = dict(schema=SCHEMA, parser=PARSER, entity_id=event['entity_id'],
                  source_event_id=event['id'], document_id=event['document_id'],
                  available_at=event['available_at'], values=values)
    record['id'] = digest(record)
    repository.append('earnings/quarter_cells/'+event['entity_id']+'/'+record['id']+'.json.gz', record)
    return record


def load_cells(repository, entity_id):
    return [repository.store.get_json(k) for k in repository.store.list_keys('earnings/quarter_cells/'+entity_id+'/')]


def _by_event(events, records):
    events = {e['id']: e for e in events}
    result = {}
    for record in records:
        if record['source_event_id'] not in events:
            continue  # A withdrawn extraction cannot supply a single-quarter cell.
        event = events[record['source_event_id']]
        if (record['entity_id'], record['document_id'], stamp(record['available_at'])) != (
                event['entity_id'], event['document_id'], availability(event)):
            raise ValueError('单季公开时间或原文关联不一致')
        if record['source_event_id'] in result:
            raise ValueError('同一财报存在多个未决单季提取版本')
        result[record['source_event_id']] = record
    return result


def history(events, records, at, metric='core_profit'):
    versions = select(events, at)
    direct = _by_event(events, records)
    result = {}
    for p, event in sorted(versions.items(), key=lambda item: availability(item[1])):
        q = quarter(event, versions.get(previous_period(p)), metric)
        if q['value'] is not None:
            parents = [e for e in versions.values() if e['id'] in q['parents']]
            result[p] = dict(value=q['value'], parents=q['parents'], cell_ids=[],
                             available_at=max(e['available_at'] for e in parents))
        record = direct.get(event['id'])
        if record:
            for target, facts in record['values'].items():
                if metric in facts:
                    result[target] = dict(value=facts[metric]['value'], parents=[event['id']],
                                          cell_ids=[record['id']], available_at=record['available_at'])
    return result


def target(events, records, report_period, metric='core_profit'):
    label = first_disclosed_target(events, report_period, metric)
    first = [e for e in events if e['report_period'] == report_period and e['revision_kind'] == 'initial'
             and e['provenance'] in ('reconstructed_pit', 'observed_pit')]
    if not first:
        return label
    event = min(first, key=availability)
    record = _by_event(events, records).get(event['id'])
    direct = record and record['values'].get(report_period, {}).get(metric)
    if direct:
        return dict(value=direct['value'], parents=[event['id']], cell_ids=[record['id']], reason=None,
                    label_available_at=event['available_at'], report_period=report_period)
    return dict(label, cell_ids=[])
