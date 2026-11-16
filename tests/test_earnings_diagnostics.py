import unittest
from app.earnings_diagnostics import diagnose


class DiagnosticsTests(unittest.TestCase):
    def test_paired_contributions_reconcile_and_holdout_is_excluded(self):
        rows=[];predictions=[]
        for i,(actual,point,base) in enumerate(((100,130,110),(10,12,20))):
            row=dict(id=str(i),entity_id=str(i),target_period='2025-03-31',membership='verified',
                     label={'value':str(actual)},features={'x':None},feature_evidence=[])
            rows.append(row)
            for name,value in (('quantile_tree_financials',point),('seasonal_naive',base)):
                predictions.append(dict(sample_id=str(i),entity_id=str(i),actual=actual,scale=100,
                    prediction_at='2025-04-01',target_period=row['target_period'],point=value,model=name,
                    groups=dict(profit_type='unclassified',target_quarter='1',retired='no')))
        rows.append(dict(rows[0],id='heldout',target_period='2026-06-30',label={'value':None,'reason':'hidden'}))
        result=diagnose(dict(rows=rows,id='panel',data_version='data'),
                        dict(id='report',predictions=predictions,policy=dict(evaluation_start='2021-11-14',holdout_start='2026-06-30')),
                        {str(i):dict(name=str(i),symbol=str(i)) for i in range(2)})
        self.assertAlmostEqual(result['overall']['scaled_mae_difference'],.06)
        self.assertAlmostEqual(sum(g['contribution_to_total_mae_difference'] for g in result['groups']['entity_id']),.06)
        self.assertEqual(result['overall']['wins'],1)
        self.assertNotIn('hidden',result['missing_label_reasons'])
