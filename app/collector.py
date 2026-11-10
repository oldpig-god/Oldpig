"""Serial, bounded collector sharing a process lock with the web app and CLI."""
import argparse
import importlib.metadata
import json
import subprocess
import sys
import time
from datetime import timedelta
from . import core, process_lock


def fetch(endpoint, parameters):
    result=subprocess.run([sys.executable,'-m','app.provider'],cwd=core.ROOT,
                          input=json.dumps(dict(endpoint=endpoint,parameters=parameters)),
                          text=True,encoding='utf-8',capture_output=True,timeout=25,
                          env=dict(core.os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1'))
    if result.returncode:raise RuntimeError(result.stderr.strip().splitlines()[-1][:500] if result.stderr.strip() else 'Provider exited without data')
    rows=json.loads(result.stdout)
    if not isinstance(rows,list):raise ValueError('Unexpected payload')
    return rows


def refresh_calendar():
    try:
        rows=fetch('tool_trade_date_hist_sina',{})
        dates=sorted(set(str(r['trade_date'])[:10] for r in rows))
        if not dates:raise ValueError('Empty calendar')
        with core.db() as c:
            c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('calendar',core.dumps(dict(dates=dates,fetched_at=core.iso()))))
        return dict(status='ok',start=dates[0],end=dates[-1])
    except Exception as e:return dict(status='error',message=str(e))


def parameters(symbol, interval, full=False):
    item=core.instrument(symbol)
    if interval=='1d':
        endpoint='fund_etf_hist_em' if item['kind']=='etf' else 'stock_zh_a_hist'
        start=core.history_start().replace('-','')
        if not full:
            with core.db() as c:r=c.execute('SELECT event_time FROM watermarks WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
            if r:start=max(start,(core.datetime.fromisoformat(r['event_time'])-timedelta(days=14)).strftime('%Y%m%d'))
        local=core.now().astimezone(core.SH)
        # Do not repeatedly request the incomplete daily candle during market hours.
        end=local if local.hour>=16 else local-timedelta(days=1)
        return endpoint,dict(symbol=item['code'],period='daily',start_date=start,end_date=end.strftime('%Y%m%d'),adjust='')
    endpoint='fund_etf_hist_min_em' if item['kind']=='etf' else 'stock_zh_a_hist_min_em'
    # Source only exposes a recent window; persist it locally every time, retaining revisions.
    return endpoint,dict(symbol=item['code'],period='1' if interval=='1m' else '5',adjust='')


def collect(symbols=None, intervals=('1d','1m','5m'), full=False, progress=None):
    core.init();known=core.universe();symbols=symbols or list(known)
    if any(s not in known for s in symbols):raise ValueError('Unknown symbol')
    if any(i not in ('1d','1m','5m') for i in intervals):raise ValueError('Unknown interval')
    with (core.DATA/'collector.lock').open('a+b') as lock:
        try:process_lock.acquire(lock)
        except BlockingIOError:return dict(status='busy',results=[])
        results=[];version=importlib.metadata.version('akshare')
        for symbol in symbols:
            for interval in intervals:
                endpoint,params=parameters(symbol,interval,full)
                batch=core.begin_batch(symbol,interval,endpoint,params,version)
                try:
                    for attempt in range(2):
                        try:rows=fetch(endpoint,params);break
                        except Exception:
                            if attempt:raise
                            time.sleep(2)
                    row=core.publish(batch,rows)
                except Exception as exc:
                    core.fail_batch(batch,str(exc));row=dict(batch_id=batch,symbol=symbol,interval=interval,status='error',error=str(exc))
                results.append(row)
                if progress:progress(row)
                time.sleep(.35)
        return dict(status='done',results=results)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='*');parser.add_argument('--intervals',nargs='+',default=['1d','1m','5m']);parser.add_argument('--full',action='store_true');parser.add_argument('--calendar',action='store_true')
    args=parser.parse_args();core.init()
    if args.calendar:print(json.dumps(refresh_calendar(),ensure_ascii=False),flush=True)
    print(json.dumps(collect(args.symbols,args.intervals,args.full,lambda r:print(json.dumps(r,ensure_ascii=False),flush=True)),ensure_ascii=False))
