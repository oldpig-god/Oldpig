"""Re-extract archived financial reports by company, preserving previous data versions."""
import argparse,json,sys
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.repository import Repository,LocalStore
from app.earnings.refresh import refresh


def refresh_company(data_dir,entity,reviews):
    return refresh(Repository(LocalStore(data_dir)),entities=[entity],reviews=reviews)


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--reviews',default='research/earnings/cell-reviews.json');p.add_argument('--workers',type=int,default=2)
    a=p.parse_args();r=Repository(LocalStore(a.data_dir));reviews=json.loads(Path(a.reviews).read_text())
    entities=sorted({k.split('/')[2] for k in r.store.list_keys('earnings/events/')});results=[]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        jobs=[pool.submit(refresh_company,a.data_dir,e,reviews) for e in entities]
        for job in as_completed(jobs):
            results+=job.result();Path(a.output).write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps(dict(completed=len(results),total=len(entities),last=results[-1]),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
