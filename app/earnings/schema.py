"""Data contracts. Money is stored as decimal yuan, timestamps include timezones."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re

SH = timezone(timedelta(hours=8))
SCHEMA = 'earnings-data-v1'
UNITS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000), '百万元': Decimal(1000000), '亿元': Decimal(100000000)}
METRICS = {
    'revenue': ('ytd', '营业总收入'), 'operating_revenue': ('ytd', '营业收入'),
    'parent_profit': ('ytd', '归母净利润'), 'core_profit': ('ytd', '扣非归母净利润'),
    'operating_cost': ('ytd', '营业成本'), 'total_operating_cost': ('ytd', '营业总成本'),
    'selling_expense': ('ytd', '销售费用'), 'administrative_expense': ('ytd', '管理费用'),
    'research_expense': ('ytd', '研发费用'), 'finance_expense': ('ytd', '财务费用'),
    'credit_impairment': ('ytd', '信用减值损失'), 'asset_impairment': ('ytd', '资产减值损失'),
    'income_tax': ('ytd', '所得税费用'), 'minority_profit': ('ytd', '少数股东损益'),
    'net_profit': ('ytd', '净利润'), 'operating_profit': ('ytd', '营业利润'),
    'total_profit': ('ytd', '利润总额'), 'operating_cashflow': ('ytd', '经营现金流净额'),
    'capital_expenditure': ('ytd', '购建长期资产支付现金'),
    'assets': ('instant', '总资产'), 'liabilities': ('instant', '总负债'),
    'inventory': ('instant', '存货'), 'receivables': ('instant', '应收账款'),
    'contract_liabilities': ('instant', '合同负债'), 'cash': ('instant', '货币资金'),
    'parent_equity': ('instant', '归母净资产'),
    'minority_equity': ('instant','少数股东权益'), 'total_equity': ('instant','所有者权益'),
}

class DataError(ValueError):
    """A record does not satisfy the earnings data contract."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:
        raise DataError('时间戳需要时区')
    return result.astimezone(SH)


def available_time(value, precision):
    if precision == 'day':
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), SH) + timedelta(days=1)
    if precision == 'second':
        return stamp(value)
    raise DataError('公开时间精度应为 day 或 second')


def period(value):
    d = date.fromisoformat(value)
    if d.strftime('%m-%d') not in ('03-31', '06-30', '09-30', '12-31'):
        raise DataError('报告期需要是自然季度末')
    return d


def money(raw, unit):
    text=str(raw).replace(',', '').replace('，', '').replace('−', '-').replace('－','-').strip()
    if text.startswith(('(', '（')) and text.endswith((')', '）')):text='-'+text[1:-1]
    value = Decimal(text)
    if not value.is_finite():
        raise DataError('金额需要是有限数值')
    return format(value * UNITS[unit], 'f')


def identity(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value) or '..' in value:
        raise DataError('记录标识格式不符')
    return value


def make_event(document, report_period, facts, *, provenance='reconstructed_pit',
               supersedes=None, revision_kind='initial', scope_version='cn-gaap-consolidated-v1',
               original_published_at=None, scope_evidence=None):
    """Original PDFs are retrospective evidence; observed snapshots start at capture time."""
    period(report_period)
    if provenance not in ('reconstructed_pit', 'observed_pit', 'latest_only', 'timestamp_estimated'):
        raise DataError('未知时间证据等级')
    if revision_kind not in ('initial', 'correction', 'restatement'):
        raise DataError('未知修订类型')
    if revision_kind != 'initial' and not supersedes:
        raise DataError('修订需要关联前一版本')
    public = available_time(document['published_at'], document['time_precision'])
    captured = stamp(document['ingested_at'])
    future = date.fromisoformat(document['published_at'])>captured.date() if document['time_precision']=='day' else public>captured
    if future:raise DataError('抓取时尚未公开的记录')
    normalized = {}
    for metric, proof in facts.items():
        if metric not in METRICS:
            raise DataError('未登记财务字段：' + metric)
        normalized[metric] = dict(value=money(proof['raw_value'], proof['unit']), unit='CNY',
            basis=METRICS[metric][0], proof=proof)
    if not normalized:
        raise DataError('没有提取到财务字段')
    if provenance == 'reconstructed_pit' and document['media_type'] != 'application/pdf':
        raise DataError('重建历史版本需要原始公告 PDF')
    effective = max(public, captured) if provenance == 'observed_pit' else public
    event = dict(schema=SCHEMA, entity_id=identity(document['entity_id']), symbol=document['symbol'],
        name=document['name'], report_period=report_period, period_start=report_period[:4]+'-01-01',
        source_id=document['source_id'], document_id=document['id'], content_hash=document['content_hash'],
        published_at=original_published_at or document['published_at'],
        version_published_at=document['published_at'], time_precision=document['time_precision'],
        available_at=effective.isoformat(), ingested_at=document['ingested_at'], provenance=provenance,
        revision_kind=revision_kind, supersedes=supersedes, currency='CNY', accounting_scope='consolidated',
        scope_version=scope_version, scope_evidence=scope_evidence, facts=normalized)
    event['id'] = digest(event)
    return event
