"""Register and run a fixed-date cumulative-profit guidance benchmark."""
import argparse,json,platform,subprocess,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.earnings.repository import Repository,LocalStore
from app.earnings.sync import ManifestStore
from app.earnings.guidance_eval import evaluate_guidance,summarize
from app.earnings.experiments import engine_version
from app.earnings.schema import digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--manifest',required=True)
    p.add_argument('--output',required=True);a=p.parse_args();r=Repository(LocalStore(a.data_dir));f=Repository(ManifestStore(r.store,a.manifest))
    policy=dict(evaluation_start='2021-11-14',holdout_start='2026-06-30',offset_days=[1,15,30],target_basis='ytd',metric='core_profit',target_quarters=[1,2,4])
    registration=dict(data_version=a.manifest,code_version=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        engine_hash=engine_version(),python=platform.python_version(),model_config=dict(model='disclosed_ytd_guidance_midpoint'),
        evaluation_policy=policy,sample_policies=[dict(schedule='quarter_end_plus_calendar_days',time='08:30+08:00')])
    registration['id']=digest(registration);r.append('earnings/experiments/registrations/'+registration['id']+'.json.gz',registration)
    rows=[];exclusions=Counter();records=[f.store.get_json(k) for k in f.store.list_keys('earnings/guidance/')]
    entities=sorted({k.split('/')[2] for k in f.store.list_keys('earnings/events/')})
    for entity in entities:
        predictions,skips=evaluate_guidance(f.events(entity),[v for v in records if v['entity_id']==entity])
        rows+=predictions;exclusions.update(skips)
    result=dict(experiment_id=registration['id'],policy=policy,data_version=a.manifest,
        target='累计扣非归母净利润，同时间同样本对照',groups=summarize(rows),predictions=rows,exclusions=dict(exclusions),
        range_note='公司预告上下限不是校准后的80%概率区间',
        coverage=dict(announcements=len(records),with_core=sum('core_profit' in v['facts'] for v in records),
                      with_parent=sum('parent_profit' in v['facts'] for v in records)))
    result['id']=digest(result);r.append('earnings/experiments/guidance_results/'+registration['id']+'.json.gz',result)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='predictions'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
