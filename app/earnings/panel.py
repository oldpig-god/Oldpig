"""Resumable research-panel collection using archived original files."""
from .cninfo import Source
from .schema import digest
from .schema import DataError
from .service import COLLECTOR_VERSION,collect
import requests


class ArchivedSource(Source):
    def __init__(self, repository):
        super().__init__(min_interval=.4)
        self.repository=repository
        self.documents={}
        for key in repository.store.list_keys('earnings/documents/'):
            document=repository.store.get_json(key)
            self.documents[document['announcement_id']]=document

    def download(self, announcement):
        document=self.documents.get(announcement['announcementId'])
        if document:
            return document['source_url'],self.repository.content(document)
        return super().download(announcement)


def collect_panel(repository, symbols, start, end, *, reviews=None, max_reports=40, progress=print):
    plan=dict(symbols=list(dict.fromkeys(symbols)),start=start,end=end,max_reports=max_reports,
              reviews=reviews or {},collector_version=COLLECTOR_VERSION)
    plan['id']=digest(plan)
    repository.append('earnings/panels/'+plan['id']+'.json.gz',plan)
    completed={}
    for key in repository.store.list_keys('earnings/runs/'):
        run=repository.store.get_json(key)
        if (run['start'],run['end'])!=(start,end):continue
        if run.get('collector_version')!=COLLECTOR_VERSION:continue
        if run['remaining'] or any(r['status']=='source_error' for r in run['outcomes']):continue
        completed[run['symbol']]=run
    source=ArchivedSource(repository)
    results=[]
    for symbol in plan['symbols']:
        existing=symbol in completed
        try:
            result=completed[symbol] if existing else collect(repository,symbol,start,end,
                source=source,reviews=reviews,max_reports=max_reports,reuse_events=True)
        except (requests.RequestException,DataError) as error:
            summary=dict(symbol=symbol,plan_id=plan['id'],status='source_error',
                         error=type(error).__name__,message=str(error) if isinstance(error,DataError) else type(error).__name__)
            summary['id']=digest(summary)
            repository.append('earnings/panels/failures/'+summary['id']+'.json.gz',summary)
            results.append(summary);progress(summary)
            continue
        statuses={s:sum(r['status']==s for r in result['outcomes']) for s in {r['status'] for r in result['outcomes']}}
        summary=dict(symbol=symbol,name=result['name'],entity_id=result['entity_id'],
                     run_id=result['id'],resumed=existing,remaining=result['remaining'],statuses=statuses)
        results.append(summary)
        progress(summary)
    return dict(plan_id=plan['id'],companies=results)
