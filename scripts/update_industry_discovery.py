"""Run a bounded discovery batch locally or against configured private cloud storage."""
import argparse,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import core,industry_discovery,daily_sync

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cloud',action='store_true');parser.add_argument('--rounds',type=int,default=1);args=parser.parse_args()
    if args.cloud:
        from app.cloud_store import CloudStore
        store=CloudStore();store.timeout=20;daily_sync.initialize(store)
    else:
        from app.strategy_service import LocalStore
        store=LocalStore()
    calendar=core.calendar_dates();target=daily_sync.freshness(None,calendar)['expected_date']
    if target is None:raise SystemExit('请先更新交易日历')
    for _ in range(args.rounds):
        result=industry_discovery.sync_tick(store,target,calendar);print(core.dumps(result),flush=True)
        if result['complete']:break
        state=store.get_json(industry_discovery.STATE)
        if state.get('retry_at',0)>time.time():break

if __name__=='__main__':main()
