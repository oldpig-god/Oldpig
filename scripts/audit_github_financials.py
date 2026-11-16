"""Inspect commit-pinned financial CSVs as external comparison evidence, not PIT labels."""
import argparse,csv,hashlib,io,json,sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.repository import Repository,LocalStore
from app.earnings.schema import SH


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True)
    p.add_argument('--inventory',default='research/earnings/github-data-inventory.json')
    p.add_argument('--output',default='research/earnings/github-csv-audit.json');a=p.parse_args()
    candidate=next(x for x in json.loads(Path(a.inventory).read_text()) if x['repo']=='henrywuu91/quant-data')
    files=[x['path'] for x in candidate['files'] if x['path'].startswith('stock/cn/') and x['path'].endswith('_finance_raw.csv')]
    root=Path(a.data_dir)/'external-review'/'quant-data'/candidate['commit'];root.mkdir(parents=True,exist_ok=True)
    base='https://raw.githubusercontent.com/'+candidate['repo']+'/'+candidate['commit']+'/'
    def fetch(path):
        response=requests.get(base+path,timeout=30);response.raise_for_status();content=response.content
        (root/Path(path).name).write_bytes(content)
        rows=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        return dict(path=path,url=base+path,sha256=hashlib.sha256(content).hexdigest(),rows=rows)
    downloaded=list(ThreadPoolExecutor(max_workers=4).map(fetch,files))
    rows=[r for f in downloaded for r in f['rows']]
    recent=[r for r in rows if '2021-11-14'<=r['report_date']<='2026-11-15']
    repo=Repository(LocalStore(a.data_dir));first={}
    for entity in sorted({k.split('/')[2] for k in repo.store.list_keys('earnings/events/')}):
        for event in repo.events(entity):
            if event['revision_kind']=='initial':first.setdefault((event['symbol'].split('.')[0],event['report_period']),event)
    comparisons=[]
    for r in recent:
        event=first.get((r['code'],r['report_date']))
        if not event:continue
        fields=[]
        for column,metric in [('assets_total','assets'),('net_income_parent','parent_profit')]:
            if metric=='parent_profit' and not r['report_date'].endswith('-03-31'):continue
            fact=event['facts'].get(metric)
            if not r[column] or not fact:continue
            difference=Decimal(r[column])-Decimal(fact['value'])
            fields.append(dict(csv_field=column,original_field=metric,csv_value=r[column],original_value=fact['value'],
                               difference=str(difference),equal=abs(difference)<=Decimal('1')))
        comparisons.append(dict(symbol=event['symbol'],report_period=r['report_date'],
            csv_filing_date=r['filing_date'],original_publication_date=event['version_published_at'],
            same_disclosure_day=r['filing_date']==event['version_published_at'],
            original_event_id=event['id'],fields=fields))
    dates=[r['report_date'] for r in rows];fields=[f for r in comparisons for f in r['fields']]
    result=dict(repo=candidate['repo'],commit=candidate['commit'],retrieved_at=datetime.now(SH).isoformat(),
        access='public_csv',usage='external_crosscheck_only',provenance='latest_only',
        license_note='README限非商业学习研究；未向模型数据或公开复现包复制第三方原始CSV',
        files=[{k:v for k,v in f.items() if k!='rows'} for f in downloaded],
        companies=len({r['code'] for r in rows}),rows=len(rows),recent_rows=len(recent),
        first_period=min(dates),last_period=max(dates),columns=list(rows[0]),
        missing_filing_dates=sum(not r['filing_date'] for r in rows),
        duplicated_company_periods=sum(v-1 for v in Counter((r['code'],r['report_date']) for r in rows).values()),
        has_core_profit='core_profit' in rows[0] or 'profit_dedt' in rows[0],
        has_revision_columns=any(k in rows[0] for k in ('revision_id','supersedes','version_published_at')),
        disclosure_matches=sum(r['same_disclosure_day'] for r in comparisons),disclosure_compared=len(comparisons),
        numerical_matches=sum(f['equal'] for f in fields),numerical_compared=len(fields),comparisons=comparisons)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('files','comparisons','columns')},ensure_ascii=False))


if __name__=='__main__':main()
