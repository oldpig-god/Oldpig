"""Original-report ingestion and data-quality receipts. No models run here."""
from datetime import datetime
from decimal import Decimal
import re
import requests
from .cninfo import Source, metadata, report_period
from .features import registry, snapshot
from .pdf_extract import compact, extract, pages_from_pdf, reviewed_cells
from .repository import PREFIX, Repository
from .schema import SCHEMA, SH, DataError, available_time, digest, make_event
from .pit import select


def accounting_checks(event):
    facts=event['facts'];issues=[]
    def equation(keys, signs, name):
        if not all(k in facts for k in keys):return
        residual=sum(Decimal(facts[k]['value'])*s for k,s in zip(keys,signs))
        tolerance=sum(Decimal(1) if facts[k]['proof']['unit']=='元' else Decimal(1000)
            if facts[k]['proof']['unit']=='千元' else Decimal(10000) for k in keys)
        if abs(residual)>tolerance:issues.append(dict(check=name,residual=str(residual)))
    equation(['assets','liabilities','total_equity'],[1,-1,-1],'assets=liabilities+equity')
    equation(['net_profit','parent_profit','minority_profit'],[1,-1,-1],'net_profit=parent+minority')
    return issues


def collect(repository, symbol, start, end, *, source=None, reviews=None, max_reports=24):
    source=source or Source();reviews=reviews or {}
    company,announcements=source.announcements(symbol.split('.')[0],start,end)
    selected=[a for a in announcements if report_period(a['announcementTitle'])]
    selected.sort(key=lambda a:(a['announcementTime'],a['announcementId']))
    outcomes=[]
    for announcement in selected[:max_reports]:
        item=dict(announcement_id=announcement['announcementId'],title=announcement['announcementTitle'],
            report_period=report_period(announcement['announcementTitle']))
        try:
            if announcement['secCode']!=symbol.split('.')[0]:raise DataError('公告证券代码不符')
            url,content=source.download(announcement)
            doc=repository.save_document(metadata(company,symbol,announcement,url,datetime.now(SH).isoformat()),content)
            if '更正公告' in announcement['announcementTitle']:
                item.update(status='announcement_only',document_id=doc['id'],related_years=re.findall(r'20\d{2}',announcement['announcementTitle']))
                outcomes.append(item)
                continue
            pages=pages_from_pdf(content)
            code=symbol.split('.')[0]
            if not re.search(r'(?:证券代码|股票代码|公司代码)[：:]?'+code,compact(''.join(pages[:10]))):
                raise DataError('原文证券代码需要核对')
            if '人民币' not in compact(''.join(pages)):raise DataError('原文人民币口径需要核对')
            facts,issues=extract(pages,item['report_period'])
            review=reviews.get(announcement['announcementId'],{})
            facts.update(reviewed_cells(pages,review.get('cells',{})))
            # Unreviewed report perimeters have separate identities until a comparison review links them.
            scope=review.get('scope_version','unreviewed-'+doc['id'])
            corrected=any(word in announcement['announcementTitle'] for word in ['更正','修订','更新'])
            revision=review.get('revision_kind','correction' if corrected else 'initial')
            previous=review.get('supersedes')
            if corrected and not previous:
                older=[e for e in repository.events(doc['entity_id']) if e['report_period']==item['report_period']]
                tip=select(older,available_time(doc['published_at'],doc['time_precision']).isoformat()).get(item['report_period'])
                previous=tip['id'] if tip else None
            if corrected and not previous:
                item.update(status='needs_revision_link',document_id=doc['id'],fields=len(facts))
            elif not facts:
                item.update(status='needs_extraction',document_id=doc['id'],issues=issues)
            else:
                parent=next((e for e in repository.events(doc['entity_id']) if e['id']==previous),None) if previous else None
                event=make_event(doc,item['report_period'],facts,supersedes=previous,
                    revision_kind=revision,scope_version=scope,original_published_at=review.get('original_published_at') or (parent['published_at'] if parent else None),scope_evidence=review.get('basis'))
                failures=accounting_checks(event)
                if failures:item.update(status='needs_accounting_review',document_id=doc['id'],checks=failures)
                else:
                    created=repository.save_event(event,pages)
                    item.update(status='created' if created else 'existing',document_id=doc['id'],event_id=event['id'],
                        fields=len(facts),core_profit=event['facts'].get('core_profit',{}).get('value'),
                        available_at=event['available_at'],scope_reviewed=bool(review.get('scope_version')),
                        issues=issues,proofs={k:v for k,v in facts.items() if k in ('core_profit','parent_profit','assets')})
        except (DataError,ValueError,KeyError,IndexError) as exc:
            item.update(status='data_error',error=str(exc))
        except requests.RequestException as exc:
            item.update(status='source_error',error=type(exc).__name__)
        outcomes.append(item)
    result=dict(schema=SCHEMA,entity_id='cninfo_'+company['orgId'],symbol=symbol,name=company['zwjc'],
        start=start,end=end,discovered=len(selected),processed=len(outcomes),remaining=max(0,len(selected)-max_reports),
        outcomes=outcomes)
    result['id']=digest(result)
    repository.append(PREFIX+'runs/'+result['id']+'.json.gz',result)
    definitions=registry();repository.append(PREFIX+'registry/'+digest(definitions)+'.json.gz',definitions)
    return result


def materialize(repository,entity_id,at,target_period):
    result=snapshot(repository.events(entity_id),entity_id,at,target_period)
    repository.save_snapshot(result)
    return result
