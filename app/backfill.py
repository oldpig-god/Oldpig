"""Resumable, four-worker daily history queue. Run separately from snapshot refresh."""
import argparse
import importlib.metadata
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from . import core,collector,history_source,process_lock


def enqueue(intervals=('1d',),retry=False):
    core.init()
    with core.db() as c:
        policy=c.execute("SELECT value FROM metadata WHERE key='history_policy'").fetchone()
        if not policy or policy['value']!='rolling_5y_v1':
            c.execute("UPDATE history_tasks SET status='pending',error=NULL WHERE interval='1d' AND status!='running'")
            c.execute("INSERT OR REPLACE INTO metadata VALUES('history_policy','rolling_5y_v1')")
        for interval in intervals:
            if interval not in ('1d','1m','5m'):raise ValueError('Invalid interval')
            c.execute("INSERT OR IGNORE INTO history_tasks(symbol,interval,status,updated_at) SELECT symbol,?,'pending',? FROM instruments WHERE active=1",(interval,core.iso()))
        if retry:c.execute("UPDATE history_tasks SET status='pending',error=NULL WHERE status IN ('error','partial','empty')")


def run(workers=4,limit=0):
    core.init()
    with (core.DATA/'backfill.lock').open('a+b') as lock:
        try:process_lock.acquire(lock)
        except BlockingIOError:return {'status':'busy'}
        with core.db() as c:
            c.execute("UPDATE batches SET status='error',error='Interrupted history worker',finished_at=? WHERE id IN (SELECT batch_id FROM history_tasks WHERE status='running') AND status='running'",(core.iso(),))
            c.execute("UPDATE history_tasks SET status='pending' WHERE status='running'")
            tasks=[dict(r) for r in c.execute("SELECT t.* FROM history_tasks t JOIN instruments i USING(symbol) WHERE i.active=1 AND t.status='pending' ORDER BY CASE t.interval WHEN '1d' THEN 0 ELSE 1 END,t.symbol LIMIT ?",(limit or -1,))]
        guard=threading.Lock();state={'completed':0,'failed_streak':0};stop=threading.Event();version=importlib.metadata.version('akshare')
        def work(task):
            if stop.is_set():return
            symbol,interval=task['symbol'],task['interval']
            with core.db() as c:
                updated=c.execute("UPDATE history_tasks SET status='running',attempts=attempts+1,updated_at=? WHERE symbol=? AND interval=? AND status='pending'",(core.iso(),symbol,interval)).rowcount
            if not updated:return
            batch=None
            try:
                endpoint,params=collector.parameters(symbol,interval,full=True)
                batch=core.begin_batch(symbol,interval,endpoint+' / direct-kline-v1' if interval=='1d' else endpoint,params,version)
                with core.db() as c:c.execute('UPDATE history_tasks SET batch_id=? WHERE symbol=? AND interval=?',(batch,symbol,interval))
                rows=history_source.fetch(core.instrument(symbol),params) if interval=='1d' else collector.fetch(endpoint,params)
                result=core.publish(batch,rows)
                status='done' if result['status']=='ok' else result['status']
                error=None
            except Exception as exc:
                status='error';error=str(exc)[:600]
                if batch:core.fail_batch(batch,error)
            with core.db() as c:
                c.execute('UPDATE history_tasks SET status=?,updated_at=?,batch_id=?,error=? WHERE symbol=? AND interval=?',(status,core.iso(),batch,error,symbol,interval))
            with guard:
                state['completed']+=1
                state['failed_streak']=state['failed_streak']+1 if status=='error' else 0
                if state['failed_streak']>=8:stop.set()
                if state['completed']%50==0 or status=='error':print(json.dumps(dict(state,symbol=symbol,status=status,total=len(tasks)),ensure_ascii=False),flush=True)
            time.sleep(.25)
        with ThreadPoolExecutor(max_workers=max(1,min(workers,4))) as pool:list(pool.map(work,tasks))
        result={'status':'source_errors_paused' if stop.is_set() else 'finished','attempted':state['completed'],'queued':len(tasks)}
        print(json.dumps(result),flush=True);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=4);p.add_argument('--limit',type=int,default=0);p.add_argument('--intervals',nargs='+',default=['1d']);p.add_argument('--retry',action='store_true');a=p.parse_args()
    enqueue(a.intervals,a.retry);run(a.workers,a.limit)
