"""Dated original guidance/express releases, with explicit profit definition and PDF cells."""
from datetime import datetime
from decimal import Decimal
import re
import requests
from .cninfo import Source,metadata
from .pdf_extract import compact,unit_in
from .schema import SH,DataError,available_time,digest,money,stamp

PARSER='guidance-table-v1'
NUMBER=r'[+-]?\d[\d,]*(?:\.\d+)?'
UNIT=r'(?:百万元|千元|万元|亿元|元)'


def release_period(title):
    title=compact(title)
    match=re.search(r'(20\d{2})年?(年度|半年度|第一季度|一季度|前三季度|第三季度|三季度)?业绩(?:预告|快报)',title)
    if not match:return None
    kind=match[2] or '年度'
    return match[1]+'-'+{'年度':'12-31','半年度':'06-30',
                        '第一季度':'03-31','一季度':'03-31','前三季度':'09-30','第三季度':'09-30','三季度':'09-30'}[kind]


def profit_metric(label):
    text=compact(label)
    if '净利润' not in text or not any(s in text for s in ('归属于','归母')):return None
    return 'core_profit' if '扣除非经常' in text else 'parent_profit'


def amount_range(text,default_unit=None):
    text=compact(text).replace('−','-')
    match=re.fullmatch(r'(盈利|亏损)?[:：]?('+NUMBER+r')('+UNIT+r')?(?:至|到|～|~|—|–|-|－)('+NUMBER+r')('+UNIT+r')?',text)
    if match:
        sign,a,au,b,bu=match.groups();unit=bu or au or default_unit
        if unit is None:return None
        values=[Decimal(money(a,au or unit)),Decimal(money(b,bu or unit))]
    else:
        match=re.fullmatch(r'(盈利|亏损)?[:：]?('+NUMBER+r')('+UNIT+r')?',text)
        if not match:return None
        sign,a,au=match.groups();unit=au or default_unit
        if unit is None:return None
        values=[Decimal(money(a,unit))]*2
    if sign=='亏损':values=[-abs(v) for v in values]
    return dict(lower=str(min(values)),upper=str(max(values)),unit='CNY')


def extract_tables(content):
    import fitz
    facts={};issues=[]
    with fitz.open(stream=content,filetype='pdf') as pdf:
        for page in pdf:
            unit=unit_in(page.get_text())
            for table in page.find_tables().tables:
                rows=table.extract();column=None
                for i,row in enumerate(rows):
                    headers=[(j,compact(v or '')) for j,v in enumerate(row)]
                    revised=[j for j,v in headers if '修正后' in v or '更正后' in v]
                    current=[j for j,v in headers if v in ('本报告期','本期','本年度','本年') or v.startswith('本报告期')]
                    if revised or current:column=(revised or current)[0];continue
                    if column is None or len(row)<=column:continue
                    label=row[0] or '';metric=profit_metric(label)
                    if metric is None:continue
                    value=row[column] or ''
                    # A merged cell may include a growth sentence below the amount.
                    value=re.split(r'比上年|同比',value)[0].strip()
                    parsed=amount_range(value,unit)
                    if parsed is None:
                        issues.append(dict(metric=metric,page=page.number+1,reason='金额单元格待核验',text=value));continue
                    rect=table.rows[i].cells[column];label_rect=table.rows[i].cells[0]
                    fact=dict(**parsed,basis='ytd',proof=dict(page=page.number+1,
                        bbox=list(rect),quote=page.get_textbox(rect),label_bbox=list(label_rect),
                        label_quote=page.get_textbox(label_rect),table_text=value))
                    if metric in facts and (facts[metric]['lower'],facts[metric]['upper'])!=(fact['lower'],fact['upper']):
                        issues.append(dict(metric=metric,page=page.number+1,reason='同一公告存在多组金额，需核验报告期'))
                        facts.pop(metric);continue
                    facts[metric]=fact
    ambiguous={i['metric'] for i in issues if '多组' in i['reason']}
    return {k:v for k,v in facts.items() if k not in ambiguous},issues


def collect_guidance(repository,symbol,start,end,source=None):
    source=source or Source(min_interval=.35)
    company,items=source.announcements(symbol.split('.')[0],start,end,'业绩')
    items=[i for i in items if release_period(i['announcementTitle'])]
    archived={repository.store.get_json(k)['announcement_id']:repository.store.get_json(k)
              for k in repository.store.list_keys('earnings/documents/cninfo_'+company['orgId']+'/')}
    outcomes=[]
    for item in sorted(items,key=lambda i:(i['announcementTime'],i['announcementId'])):
        entry=dict(announcement_id=item['announcementId'],title=item['announcementTitle'])
        try:
            if item['secCode']!=symbol.split('.')[0]:raise DataError('公告证券身份不符')
            doc=archived.get(item['announcementId'])
            if doc:content=repository.content(doc)
            else:
                url,content=source.download(item)
                doc=repository.save_document(metadata(company,symbol,item,url,datetime.now(SH).isoformat()),content)
            key='earnings/guidance/'+doc['entity_id']+'/'+digest([doc['id'],PARSER])+'.json.gz'
            existing=repository.store.get_json(key,optional=True)
            if existing:record=existing
            else:
                facts,issues=extract_tables(content)
                published=available_time(doc['published_at'],doc['time_precision'])
                observed=bool(doc.get('prior_documents'))
                available=max(published,stamp(doc['ingested_at'])) if observed else published
                record=dict(parser=PARSER,entity_id=doc['entity_id'],symbol=symbol,name=doc['name'],
                    document_id=doc['id'],source_url=doc['source_url'],content_hash=doc['content_hash'],
                    published_at=doc['published_at'],available_at=available.isoformat(),
                    provenance='observed_pit' if observed else 'reconstructed_pit',
                    target_period=release_period(doc['title']),kind='express' if '快报' in doc['title'] else 'guidance',
                    revised=any(w in doc['title'] for w in ('修正','更正','修订')),facts=facts,issues=issues,
                    status='parsed' if facts else 'needs_cell_review')
                record['id']=digest(record);repository.append(key,record)
            entry.update(status=record['status'],document_id=doc['id'],record_id=record['id'],metrics=sorted(record['facts']))
        except (DataError,ValueError) as error:entry.update(status='data_error',reason=str(error))
        except requests.RequestException as error:entry.update(status='source_error',reason=type(error).__name__)
        outcomes.append(entry)
    result=dict(kind='guidance_collection',symbol=symbol,start=start,end=end,parser=PARSER,outcomes=outcomes)
    result['id']=digest(result);repository.append('earnings/runs/'+result['id']+'.json.gz',result)
    return result


def asof(records,target_period,at,metric='core_profit'):
    """Latest announcement of each kind; an unparsed correction blocks an older amount."""
    visible=[r for r in records if r['target_period']==target_period and stamp(r['available_at'])<stamp(at)]
    result={}
    for kind in ('guidance','express'):
        candidates=[r for r in visible if r['kind']==kind]
        if not candidates:continue
        latest=max(r['available_at'] for r in candidates)
        tips=[r for r in candidates if r['available_at']==latest]
        amounts={(r['facts'].get(metric,{}).get('lower'),r['facts'].get(metric,{}).get('upper')) for r in tips}
        if len(amounts)!=1 or None in next(iter(amounts)):continue
        record=sorted(tips,key=lambda r:r['id'])[-1]
        result[kind]=dict(record['facts'][metric],record_id=record['id'],available_at=record['available_at'])
    return result
