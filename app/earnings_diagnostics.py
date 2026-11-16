"""Paired error attribution for an already frozen experiment; never trains a model."""
from collections import Counter, defaultdict


def aggregate(rows):
    n=len(rows)
    if not n:return dict(n=0)
    return dict(n=n, candidate_scaled_mae=sum(r['candidate_scaled_error'] for r in rows)/n,
                baseline_scaled_mae=sum(r['baseline_scaled_error'] for r in rows)/n,
                scaled_mae_difference=sum(r['scaled_error_difference'] for r in rows)/n,
                candidate_amount_mae=sum(r['candidate_amount_error'] for r in rows)/n,
                baseline_amount_mae=sum(r['baseline_amount_error'] for r in rows)/n,
                wins=sum(r['scaled_error_difference']<0 for r in rows),
                losses=sum(r['scaled_error_difference']>0 for r in rows))


def diagnose(bundle, report, identities, candidate='quantile_tree_financials', baseline='seasonal_naive'):
    samples={r['id']:r for r in bundle['rows']}
    models={name:{p['sample_id']:p for p in report['predictions'] if p['model']==name}
            for name in (candidate,baseline)}
    paired=[]
    for key in sorted(models[candidate].keys() & models[baseline].keys()):
        c=models[candidate][key];b=models[baseline][key];sample=samples[key]
        if (c['actual'],c['scale'],c['prediction_at'])!=(b['actual'],b['scale'],b['prediction_at']):
            raise ValueError('配对记录的标签、缩放或预测时点不同')
        ce=abs(c['point']-c['actual']);be=abs(b['point']-b['actual'])
        paired.append(dict(sample_id=key,entity_id=c['entity_id'],**identities[c['entity_id']],
            target_period=c['target_period'],prediction_at=c['prediction_at'],actual=c['actual'],
            candidate_point=c['point'],baseline_point=b['point'],scale=c['scale'],
            candidate_amount_error=ce,baseline_amount_error=be,candidate_scaled_error=ce/c['scale'],
            baseline_scaled_error=be/b['scale'],scaled_error_difference=(ce-be)/c['scale'],
            amount_error_difference=ce-be,groups=c['groups'],label=sample['label'],
            feature_evidence=sample['feature_evidence'],
            missing_feature_count=sum(v is None for v in sample['features'].values())))
    grouped={}
    for group in ('entity_id','target_period','profit_type','target_quarter','retired'):
        buckets=defaultdict(list)
        for row in paired:buckets[row[group] if group in row else row['groups'][group]].append(row)
        grouped[group]=[{group:key,**aggregate(rows),
                        'contribution_to_total_mae_difference':sum(r['scaled_error_difference'] for r in rows)/len(paired)}
                       for key,rows in sorted(buckets.items())]
    policy=report['policy']
    development=[r for r in bundle['rows'] if policy['evaluation_start']<=r['target_period']<policy['holdout_start']
                 and r['membership'] not in ('verified_delisted','not_yet_listed')]
    missing=Counter(r['label'].get('reason') for r in development if r['label'].get('value') is None)
    ordered=sorted(paired,key=lambda r:r['candidate_scaled_error'],reverse=True)
    return dict(candidate=candidate,baseline=baseline,data_version=bundle['data_version'],
                dataset_id=bundle['id'],report_id=report['id'],overall=aggregate(paired),groups=grouped,
                missing_label_reasons=dict(missing),
                largest_scaled_errors=[r['sample_id'] for r in ordered[:20]],
                largest_amount_errors=[r['sample_id'] for r in sorted(paired,key=lambda r:r['candidate_amount_error'],reverse=True)[:20]],
                largest_regressions=[r['sample_id'] for r in sorted(paired,key=lambda r:r['scaled_error_difference'],reverse=True)[:20]],
                concentration=[dict(removed_largest_errors=k,**aggregate(ordered[k:])) for k in (1,5,10)],
                concentration_note='事后敏感性诊断，不作为删除样本或选择模型的依据',samples=paired)
