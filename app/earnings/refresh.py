"""Re-extract archived originals without downloading or changing old dataset manifests."""
from collections import defaultdict
from copy import deepcopy
from .pdf_extract import pages_from_pdf,reviewed_cells
from .quarters import save_cells
from .schema import digest, money
from .cninfo import report_period


def refresh(repository,entities=None,reviews=None):
    if entities is None:entities=sorted({k.split('/')[2] for k in repository.store.list_keys('earnings/events/')})
    reviews=reviews or {}
    outcomes=[]
    for entity in entities:
        replacements={}
        revised=0
        direct=0
        originals={}
        for old in repository.events(entity):
            document=repository.store.get_json('earnings/documents/'+entity+'/'+old['document_id']+'.json.gz')
            if report_period(document['title'])!=old['report_period']:
                repository.withdraw(entity,old['id'],'标题核验：非正式季度/年度财务报告；保留原文')
                continue
            content=repository.content(document)
            pages=pages_from_pdf(content)
            originals[old['document_id']]=document
            from .summary_tables import extract_core_pdf
            core=extract_core_pdf(content,pages,old['report_period'])
            cells=reviews.get(document['announcement_id'],{}).get('cells',{})
            if 'core_profit' in cells:core=reviewed_cells(pages,{'core_profit':cells['core_profit']},content)['core_profit']
            event=deepcopy(old)
            if core:
                p=core
                event['facts']['core_profit']=dict(value=money(p['raw_value'],p['unit']),unit='CNY',basis='ytd',proof=p)
            else:
                event['facts'].pop('core_profit',None)
            if event['supersedes'] in replacements:event['supersedes']=replacements[event['supersedes']]
            event['id']=digest({k:v for k,v in event.items() if k!='id'})
            if event['id']!=old['id']:
                repository.save_event(event,pages)
                repository.withdraw(entity,old['id'],'原文重新提取，替代事件：'+event['id'])
                replacements[old['id']]=event['id']
                revised+=1
            direct+=bool(save_cells(repository,event,pages,content))
        by_period=defaultdict(list)
        for event in repository.events(entity):
            if event['revision_kind']=='initial':by_period[event['report_period']].append(event)
        duplicates=0
        redirects={}
        for period,events in by_period.items():
            if len(events)<2:continue
            if len({e['available_at'] for e in events})!=1:continue
            best=max(events,key=lambda e:('正文' not in originals[e['document_id']]['title'],len(e['facts'])))
            for event in events:
                if event['id']==best['id']:continue
                common=event['facts'].keys() & best['facts'].keys()
                if common and all(event['facts'][k]['value']==best['facts'][k]['value'] for k in common):
                    repository.withdraw(entity,event['id'],'同日正文/全文重复披露，重合字段一致；采用：'+best['id'])
                    redirects[event['id']]=best['id']
                    duplicates+=1
        # Corrections keep their revision chain when an equivalent duplicate root was retired.
        for old in repository.events(entity):
            if old['supersedes'] not in redirects:continue
            event=deepcopy(old)
            event['supersedes']=redirects[old['supersedes']]
            event['id']=digest({k:v for k,v in event.items() if k!='id'})
            document=originals[event['document_id']]
            pages=pages_from_pdf(repository.content(document))
            repository.save_event(event,pages)
            repository.withdraw(entity,old['id'],'重复披露归并后更新修订链：'+event['id'])
            redirects[old['id']]=event['id']
            save_cells(repository,event,pages)
        outcomes.append(dict(entity_id=entity,reextracted=revised,direct_quarter_reports=direct,duplicate_reports=duplicates))
    return outcomes


def relink_archived_corrections(repository,reviews=None):
    """Retry archived correction documents after originals and duplicate roots are resolved."""
    from .service import collect
    active={e['document_id'] for entity in {k.split('/')[2] for k in repository.store.list_keys('earnings/events/')}
            for e in repository.events(entity)}
    documents=[repository.store.get_json(k) for k in repository.store.list_keys('earnings/documents/')]
    pending=[d for d in documents if d['id'] not in active and report_period(d['title'])
             and any(word in d['title'] for word in ('更正','修订','更新')) and '更正公告' not in d['title']]
    results=[]
    for document in sorted(pending,key=lambda d:(d['published_at'],d['announcement_id'])):
        class ArchivedCorrection:
            def announcements(self,*args):
                return dict(orgId=document['entity_id'].removeprefix('cninfo_'),zwjc=document['name']),[document['publication_metadata']]
            def download(self,announcement):
                return document['source_url'],repository.content(document)
        result=collect(repository,document['symbol'],document['published_at'],document['published_at'],
                       source=ArchivedCorrection(),reviews=reviews,max_reports=1)
        if any(o.get('event_id') for o in result['outcomes']):
            pages=pages_from_pdf(repository.content(document))
            for event in repository.events(document['entity_id']):
                if event['document_id']==document['id']:save_cells(repository,event,pages)
        results+=result['outcomes']
    return results
