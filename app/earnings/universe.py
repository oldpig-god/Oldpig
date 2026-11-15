"""Dated company/alias, listing, industry and size facts share the same as-of clock."""
from .schema import DataError, available_time, digest, stamp

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


def seed_report_membership(repository):
    """An identified public stock report establishes membership from that publication onward.

    This is an evidence start date, not an inferred IPO date. It never extends membership
    back to the financial period to which a later report happens to refer.
    """
    results=[]
    candidates_by_entity={}
    entities=sorted({k.split('/')[2] for k in repository.store.list_keys('earnings/events/')})
    for entity in entities:
        for event in repository.events(entity):
            if event['provenance']=='reconstructed_pit':
                doc=repository.store.get_json('earnings/documents/'+entity+'/'+event['document_id']+'.json.gz')
                candidates_by_entity.setdefault(entity,{})[doc['id']]=doc
    # Identity checks precede financial extraction. A valid issuer with no parsed
    # financial fields must remain in the coverage denominator.
    from .cninfo import report_period
    for key in repository.store.list_keys('earnings/runs/'):
        run=repository.store.get_json(key);entity=run['entity_id']
        for outcome in run['outcomes']:
            if outcome['status'] not in ('needs_extraction','needs_accounting_review','needs_revision_link'):continue
            doc=repository.store.get_json('earnings/documents/'+entity+'/'+outcome['document_id']+'.json.gz')
            if report_period(doc['title']):candidates_by_entity.setdefault(entity,{})[doc['id']]=doc
    for entity,documents in sorted(candidates_by_entity.items()):
        candidates=list(documents.values())
        if not candidates:continue
        document=min(candidates,key=lambda d:available_time(d['published_at'],d['time_precision']))
        day=document['published_at'][:10]
        record=dict(entity_id=entity,symbol=document['symbol'],field='listed',value=True,
                    published_at=document['published_at'],time_precision=document['time_precision'],
                    ingested_at=document['ingested_at'],effective_at=day+'T00:00:00+08:00',
                    provenance='reconstructed_pit',document_id=document['id'],source_url=document['source_url'],
                    basis='已核对原始股票报告证券代码；仅证明从公开日起属于证券池，不推断IPO日期')
        record['id']=digest(record)
        repository.append('earnings/universe/'+entity+'/'+record['id']+'.json.gz',record)
        results.append(record)
    return results


def collect_retirement(repository,symbol,start,end,source=None):
    """Require an explicit delisting-date sentence in the original official notice."""
    from datetime import date,datetime
    import re
    from .cninfo import Source,metadata
    from .pdf_extract import compact,pages_from_pdf
    from .schema import SH
    source=source or Source()
    company,announcements=source.announcements(symbol.split('.')[0],start,end,keyword='摘牌')
    verified=[]
    for announcement in sorted(announcements,key=lambda a:a['announcementTime']):
        title=announcement['announcementTitle']
        if '摘牌' not in title or '终止上市' not in title or announcement['secCode']!=symbol.split('.')[0]:continue
        url,content=source.download(announcement)
        doc=repository.save_document(metadata(company,symbol,announcement,url,datetime.now(SH).isoformat()),content)
        pages=pages_from_pdf(content)
        if not re.search(r'(?:证券代码|股票代码)[：:]?'+symbol.split('.')[0],compact(''.join(pages[:5]))):
            raise DataError('摘牌原文证券代码未匹配')
        for index,page in enumerate(pages[:5]):
            lines=page.splitlines()
            for n in range(len(lines)):
                quote='\n'.join(lines[n:n+3]);text=compact(quote)
                match=re.search(r'(?:摘牌日期|摘牌日)[：:为]*(20\d{2})年(\d{1,2})月(\d{1,2})日',text)
                if not match:continue
                effective=date(*map(int,match.groups())).isoformat()
                record=dict(entity_id=doc['entity_id'],symbol=symbol,field='delisted',value=True,
                    published_at=doc['published_at'],time_precision=doc['time_precision'],ingested_at=doc['ingested_at'],
                    effective_at=effective+'T00:00:00+08:00',provenance='reconstructed_pit',document_id=doc['id'],
                    source_url=url,proof=dict(page=index+1,quote=quote))
                record['id']=digest(record)
                repository.append('earnings/universe/'+doc['entity_id']+'/'+record['id']+'.json.gz',record)
                verified.append(record)
                break
            else:continue
            break
    return dict(symbol=symbol,status='verified' if verified else 'explicit_date_not_found',records=verified)
