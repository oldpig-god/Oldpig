"""Archive original forecast/express releases for the declared research universe."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.repository import Repository,LocalStore
from app.earnings.guidance import collect_guidance
from app.earnings.cninfo import Source


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True)
    p.add_argument('--symbols-file',required=True);p.add_argument('--start',default='2021-01-01')
    p.add_argument('--end',default='2026-05-31');p.add_argument('--output',required=True)
    a=p.parse_args();r=Repository(LocalStore(a.data_dir));source=Source(min_interval=.35);results=[]
    for symbol in json.loads(Path(a.symbols_file).read_text()):
        result=collect_guidance(r,symbol,a.start,a.end,source);results.append(result)
        Path(a.output).write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(dict(symbol=symbol,announcements=len(result['outcomes']),
            parsed=sum(x['status']=='parsed' for x in result['outcomes'])),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
