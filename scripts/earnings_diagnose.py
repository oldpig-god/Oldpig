"""Trace fixed-model errors back to original financial report cells."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.repository import Repository,LocalStore
from app.earnings.dataset import load_panel
from app.earnings.sync import ManifestStore
from app.earnings_diagnostics import diagnose


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--summary',default='research/earnings/benchmark-summary.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    repo=Repository(LocalStore(args.data_dir));summary=json.loads(Path(args.summary).read_text())
    frozen=Repository(ManifestStore(repo.store,summary['data_version']))
    bundle=load_panel(repo,summary['sample_dataset_id'])
    events={e['id']:e for entity in sorted({r['entity_id'] for r in bundle['rows']}) for e in frozen.events(entity)}
    identities={e['entity_id']:dict(symbol=e['symbol'],name=e['name']) for e in events.values()}
    trial=next(t for t in summary['trials'] if t['name']=='tree_financials')
    result=repo.store.get_json('earnings/experiments/results/'+trial['experiment_id']+'.json.gz')
    diagnosis=diagnose(bundle,result['report'],identities)
    for row in diagnosis['samples']:
        row['label_sources']=[]
        for event_id in row['label']['parents']:
            event=events[event_id]
            document=frozen.store.get_json('earnings/documents/'+event['entity_id']+'/'+event['document_id']+'.json.gz')
            source=dict(event_id=event_id,report_period=event['report_period'],title=document['title'],
                        announcement_id=document['announcement_id'],document_id=document['id'],
                        published_at=document['published_at'],url=document['source_url'],sha256=document['content_hash'],
                        cumulative_core=event['facts'].get('core_profit'))
            source['quarter_cells']=[frozen.store.get_json('earnings/quarter_cells/'+event['entity_id']+'/'+cid+'.json.gz')
                                     for cid in row['label'].get('cell_ids',[])]
            row['label_sources'].append(source)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(diagnosis,ensure_ascii=False,indent=2),encoding='utf-8')
    by_id={r['sample_id']:r for r in diagnosis['samples']}
    print(json.dumps(dict(overall=diagnosis['overall'],missing=diagnosis['missing_label_reasons'],
        largest=[{k:by_id[key][k] for k in ('name','symbol','target_period','actual','candidate_point','baseline_point','candidate_scaled_error')}
                 for key in diagnosis['largest_scaled_errors'][:12]]),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
