import copy
from dataclasses import replace
import unittest
import json

from app.earnings.evaluation import EvaluationPolicy, evaluate, interval, metrics
from app.earnings.models import TreeConfig, feature_names, shuffled_label_factory, tree_factory
from test_earnings_evaluation import panel


class QuantileTests(unittest.TestCase):
    def test_residual_restores_each_company_baseline_and_scale(self):
        rows=copy.deepcopy([r for r in panel() if r['status']=='ready'][:30])
        for i,row in enumerate(rows):
            row['baselines']['seasonal_naive']=(-1 if i%2 else 1)*(i+1)*100
            row['scale']=1000+i*50
            row['label']['value']=str(row['baselines']['seasonal_naive']+row['scale']*.02)
        predict=tree_factory(TreeConfig(iterations=3,min_samples_leaf=3),seasonal_residual=True)(rows)
        for row in rows[::7]:
            result=predict(row)['seasonal_residual_quantile_tree_financials']
            expected=row['baselines']['seasonal_naive']+row['scale']*.02
            self.assertAlmostEqual(result['point'],expected)
            self.assertAlmostEqual(result['raw_lower'],expected/row['scale'])
            self.assertAlmostEqual(result['raw_upper'],expected/row['scale'])

    def test_history_ablation_excludes_financials(self):
        fields=feature_names(TreeConfig(feature_set='profit_history'))
        self.assertFalse(any(f.endswith('_latest_to_scale') for f in fields))
        self.assertIn('core_lag_4',fields)

    def test_tree_repeat_and_non_crossing(self):
        rows=[r for r in panel() if r['status']=='ready']
        fit=tree_factory(TreeConfig(iterations=5,min_samples_leaf=3))
        a=fit(rows[:30])(rows[-1]);b=fit(rows[:30])(rows[-1])
        self.assertEqual(a,b)
        output=a['quantile_tree_financials'];middle=output['point']/rows[-1]['scale']
        self.assertLessEqual(output['raw_lower'],middle)
        self.assertLessEqual(middle,output['raw_upper'])

    def test_past_only_cqr_widens_tree_bands(self):
        policy=EvaluationPolicy(min_calibration_samples=4,min_calibration_periods=1)
        past=[dict(target_period='2024-03-31',actual_scaled=3,point_scaled=0,raw_lower=-1,raw_upper=1) for _ in range(4)]
        band=interval(0,past,policy,raw_lower=-1,raw_upper=1)
        self.assertEqual((band['lower'],band['upper']),(-3,3))

    def test_tree_evaluation_remains_temporal_and_deterministic(self):
        rows=panel();policy=EvaluationPolicy(min_training_samples=10,min_training_periods=2,
            min_calibration_samples=4,min_calibration_periods=1)
        fit=tree_factory(TreeConfig(iterations=3,min_samples_leaf=3))
        a=evaluate(rows,policy,predictor=fit);b=evaluate(json.loads(json.dumps(rows,sort_keys=True)),policy,predictor=fit)
        self.assertEqual(a,b)
        self.assertIn('quantile_tree_financials',a['models'])
        for row in a['predictions']:
            if row['model']=='quantile_tree_financials':self.assertIn('raw_lower',row)

    def test_permutation_is_training_only_and_reproducible(self):
        rows=panel()[:10];before=copy.deepcopy(rows);observed=[]
        def fit(train):observed.append(train);return lambda row:{}
        shuffled_label_factory(fit,17)(rows);shuffled_label_factory(fit,17)(rows)
        self.assertEqual(rows,before)
        self.assertEqual(observed[0],observed[1])
        self.assertNotEqual([r['label']['value'] for r in rows],[r['label']['value'] for r in observed[0]])
        self.assertEqual([r['prediction_at'] for r in rows],[r['prediction_at'] for r in observed[0]])


if __name__=='__main__':unittest.main()
