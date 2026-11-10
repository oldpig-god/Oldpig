"""Audit stored daily dates against the saved trading calendar; no network calls."""
import bisect
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import core

start=core.history_start();end=core.calendar_state().get('expected_daily');days=[d for d in core.calendar_dates() if start<=d and (not end or d<=end)]
with core.db() as c:
    spans=[dict(r) for r in c.execute("SELECT o.symbol,min(o.event_time) first,max(o.event_time) last,count(*) rows FROM observations o JOIN instruments i USING(symbol) WHERE i.active=1 AND o.interval='1d' AND o.event_time>=? GROUP BY o.symbol",(start,))]
    tasks=[dict(r) for r in c.execute('SELECT status,count(*) count FROM history_tasks WHERE interval=\'1d\' GROUP BY status')]
    c.execute('CREATE TEMP TABLE calendar_days(day TEXT PRIMARY KEY)')
    c.executemany('INSERT INTO calendar_days VALUES(?)',[(d,) for d in days])
    outside=[dict(r) for r in c.execute("SELECT o.symbol,count(*) count FROM observations o JOIN instruments i USING(symbol) LEFT JOIN calendar_days c ON c.day=substr(o.event_time,1,10) WHERE i.active=1 AND o.interval='1d' AND o.event_time>=? AND c.day IS NULL GROUP BY o.symbol",(start,))]
results=[]
for row in spans:
    first,last=row['first'][:10],row['last'][:10]
    expected=bisect.bisect_right(days,last)-bisect.bisect_left(days,first)
    results.append(dict(row,expected_sessions_between_observed_bounds=expected,count_gap=expected-row['rows']))
report={'checked_at':core.iso(),'window':core.history_window(),'last_expected_daily':end,'calendar_sessions':len(days),'symbols_with_rows':len(results),'rows':sum(r['rows'] for r in results),'queue':tasks,
        'symbols_with_count_gap':sum(r['count_gap']!=0 for r in results),'count_gap_examples':[r for r in results if r['count_gap']!=0][:30],
        'outside_calendar_symbols':len(outside),'outside_calendar_examples':outside[:30],
        'symbols_reaching_target_day':sum(r['last'][:10]==end for r in results),
        'note':'按已观察到的首尾日期核对交易日数量；不推断上市日前或停牌原因，不保证源完整性。'}
output=core.ROOT/'design/v4/history-window-audit.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
