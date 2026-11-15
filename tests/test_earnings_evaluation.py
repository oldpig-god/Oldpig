import copy
from dataclasses import replace
from datetime import date
import tempfile
import unittest
import random

from app.earnings.dataset import SamplePolicy, audit, build,load_panel,save_panel,sample, scheduled_at, shift_period
from app.earnings.evaluation import EvaluationPolicy, evaluate, interval, metrics, paired_comparison
from app.earnings.experiments import counts, evaluate_registered
from app.earnings.quarters import extract_quarters, history, save_cells, target
from app.earnings.repository import LocalStore, Repository
from app.earnings.schema import digest, make_event


def events(entity='sample', begin=2019, end=2026):
    result = []
    for year in range(begin, end+1):
        ytd = 0
        for month in (3, 6, 9, 12):
            p = date(year, month, {3:31, 6:30, 9:30, 12:31}[month]).isoformat()
            ytd += 100+10*(year-begin)+month*2
            published = (f'{year+1}-03-20' if month == 12 else
                         f'{year}-{ {3:4,6:8,9:10}[month]:02d}-20')
            document = dict(id=digest([entity,p]), entity_id=entity, symbol='000001.SZ', name=entity,
                source_id='test', content_hash='a'*64, media_type='application/pdf',
                published_at=published, time_precision='day', ingested_at='2028-01-01T12:00:00+08:00')
            facts = {k:dict(raw_value=str(v), unit='元', page=1, quote=k+' '+str(v), column='current_ytd')
                     for k,v in {'core_profit': ytd, 'assets': 10000+year, 'revenue': ytd*10}.items()}
            result.append(make_event(document, p, facts, scope_version='reviewed'))
    return result


def panel():
    rows=[]
    for entity in ('one','two','retired'):
        es=events(entity)
        for year in range(2022,2027):
            for suffix in ('03-31','06-30','09-30','12-31'):
                rows.append(sample(es,[],entity,f'{year}-{suffix}'))
    return rows


class QuarterCellTests(unittest.TestCase):
    def test_q3_direct_value_differs_from_ytd(self):
        pages=['扣除非经常性损益的净利润 30 20% 90 10%']
        facts={'core_profit':{'proof':dict(raw_value='90',unit='元',page=1,quote=pages[0],column='current_ytd')}}
        result=extract_quarters(pages,'2025-09-30',facts)
        self.assertEqual(result['2025-09-30']['core_profit']['raw_value'],'30')

    def test_annual_quarter_table_and_units(self):
        pages=['八、分季度主要财务指标\n单位：千元\n第一季度 第二季度 第三季度 第四季度\n'
               '营业收入 10 20 30 40\n归属于上市公司股东的扣除非经常性损\n1 2 -3 4 益的净利润\n'
               '九、非经常性损益项目\n扣除非经常性损益的净利润 99 99 99 99']
        values=extract_quarters(pages,'2025-12-31',{})
        self.assertEqual(values['2025-12-31']['core_profit']['raw_value'],'4')
        self.assertEqual(values['2025-09-30']['core_profit']['raw_value'],'-3')
        self.assertEqual(values['2025-06-30']['revenue']['unit'],'千元')

    def test_later_annual_table_does_not_backdate_quarter_label(self):
        es=events(begin=2024,end=2025)
        q2=next(e for e in es if e['report_period']=='2025-06-30')
        q2['scope_version']='changed'
        annual=next(e for e in es if e['report_period']=='2025-12-31')
        record=dict(id='cell',entity_id='sample',source_event_id=annual['id'],document_id=annual['document_id'],
                    available_at=annual['available_at'],values={'2025-06-30':{'core_profit':{'value':'999'}}})
        self.assertIsNone(target(es,[record],'2025-06-30')['value'])
        self.assertNotEqual(history(es,[record],'2025-10-01T08:30:00+08:00').get('2025-06-30',{}).get('value'),'999')
        self.assertEqual(history(es,[record],'2026-04-01T08:30:00+08:00')['2025-06-30']['value'],'999')

    def test_direct_cell_wrong_publication_rejected(self):
        es=events(begin=2024,end=2024);e=es[-1]
        r=dict(id='cell',entity_id='sample',source_event_id=e['id'],document_id=e['document_id'],
               available_at='2024-01-01T00:00:00+08:00',values={})
        with self.assertRaisesRegex(ValueError,'公开时间'):history(es,[r],'2026-01-01T08:30:00+08:00')


class SampleTests(unittest.TestCase):
    def test_frozen_panel_replays_without_reading_original_pdf_objects(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(LocalStore(root));rows=panel()[:3]
            bundle=save_panel(repo,rows,'frozen-input')
            self.assertEqual(load_panel(repo,bundle['id']),bundle)

    def test_missing_report_quarter_still_appears_in_population(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(LocalStore(root))
            for e in events('sample',2022,2023):
                if e['report_period']=='2023-06-30':continue
                repo.append('earnings/events/sample/'+e['report_period']+'/'+e['id']+'.json.gz',e)
            rows=build(repo,start='2022-01-01',end='2023-12-31')
            self.assertEqual(len(rows),8)
            gap=next(r for r in rows if r['target_period']=='2023-06-30')
            self.assertIsNone(gap['label']['value'])

    def test_issuer_without_extracted_financials_remains_in_coverage(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(LocalStore(root))
            record=dict(entity_id='unparsed',field='listed',value=True,published_at='2020-01-01',
                time_precision='day',effective_at='2020-01-01T00:00:00+08:00',provenance='reconstructed_pit')
            repo.append('earnings/universe/unparsed/listed.json.gz',record)
            rows=build(repo,start='2023-01-01',end='2023-12-31')
            result=evaluate(rows)
            self.assertEqual(result['research_population'],4)
            self.assertEqual(result['abstained'],4)
            self.assertEqual(result['label_missing'],4)

    def test_schedule_never_uses_ex_post_disclosure_date(self):
        self.assertEqual(scheduled_at('2025-12-31'),'2026-01-01T08:30:00+08:00')
        self.assertEqual(shift_period('2025-03-31',-1),'2024-12-31')
        self.assertEqual(shift_period('2025-12-31',1),'2026-03-31')

    def test_scaled_target_uses_already_public_asset(self):
        es=events();row=sample(es,[],'sample','2025-03-31')
        self.assertEqual(row['status'],'ready')
        self.assertEqual(row['scale'],12024)
        self.assertNotIn('label',row['features'])
        self.assertEqual(row,sample(es,[],'sample','2025-03-31'))

    def test_sparse_history_abstains(self):
        row=sample(events(begin=2025),[],'sample','2025-09-30')
        self.assertEqual(row['status'],'abstain')
        self.assertIn('历史单季样本不足',row['reasons'])

    def test_zero_and_negative_profit_are_valid(self):
        es=events()
        for e in es:e['facts']['core_profit']['value']='-100'
        row=sample(es,[],'sample','2025-03-31')
        self.assertEqual(row['baselines']['seasonal_naive'],-100)
        self.assertEqual(row['status'],'ready')

    def test_future_feature_injection_detected(self):
        row=sample(events(),[],'sample','2025-03-31')
        row['features']['oracle']=float(row['label']['value'])
        row['feature_evidence'].append(dict(kind='oracle',available_at=row['label']['label_available_at']))
        with self.assertRaisesRegex(ValueError,'之后的特征'):audit(row)


class EvaluationTests(unittest.TestCase):
    def test_retired_periods_are_business_inapplicable_not_failed_predictions(self):
        identity=dict(entity_id='sample',field='listed',value=True,published_at='2020-01-01',time_precision='day',
            effective_at='2020-01-01T00:00:00+08:00',provenance='reconstructed_pit')
        retired=dict(identity,field='delisted',published_at='2023-05-30',effective_at='2023-06-01T00:00:00+08:00')
        rows=[sample(events(),[],'sample','2023-'+s,universe=[identity,retired]) for s in ('03-31','06-30','09-30','12-31')]
        result=evaluate(rows)
        self.assertEqual(result['business_inapplicable'],3)
        self.assertEqual(result['research_population'],1)
        self.assertEqual(result['abstained'],0)
    def test_warmup_can_train_but_never_enters_report_metrics(self):
        rows=panel()
        earlier=sample(events('one'),[],'one','2020-12-31')
        result=evaluate([earlier]+rows)
        self.assertEqual(result['warmup_samples'],1)
        self.assertTrue(all(r['target_period']>='2021-11-14' for r in result['predictions']))

    def test_version_conflict_excludes_company_with_a_recorded_reason(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(LocalStore(root));bad=events('bad',2022,2022)
            duplicate=copy.deepcopy(bad[0]);duplicate['id']=digest(['duplicate',duplicate['id']])
            for e in bad+[duplicate]+events('good',2022,2023):
                repo.append('earnings/events/'+e['entity_id']+'/'+e['report_period']+'/'+e['id']+'.json.gz',e)
            excluded=[];rows=build(repo,start='2022-01-01',end='2023-12-31',exclusions=excluded)
            self.assertTrue(rows);self.assertEqual({r['entity_id'] for r in rows},{'good'})
            self.assertEqual(excluded[0]['entity_id'],'bad')
            self.assertIn('未关联',excluded[0]['reason'])

    def test_80_percent_interval_on_independent_synthetic_errors(self):
        rng=random.Random(1729)
        periods=['2023-03-31','2023-06-30','2023-09-30','2023-12-31']
        past=[dict(target_period=periods[i%4],actual_scaled=rng.gauss(0,1),point_scaled=0) for i in range(400)]
        band=interval(0,past,EvaluationPolicy())
        errors=[rng.gauss(0,1) for _ in range(2000)]
        coverage=sum(band['lower']<=e<=band['upper'] for e in errors)/len(errors)
        self.assertAlmostEqual(coverage,.8,delta=.05)

    def test_tampered_sample_version_is_rejected(self):
        row=panel()[0];row['features']['core_lag_4']=12345
        with self.assertRaisesRegex(ValueError,'版本标识'):evaluate([row])
    def test_entire_period_test_and_unpublished_training_label_excluded(self):
        rows=panel();result=evaluate(rows)
        by_id={r['id']:r for r in rows}
        for fold in result['folds']:
            for key in fold['train_sample_ids']:
                r=by_id[key]
                self.assertLess(r['target_period'],fold['target_period'])
                self.assertLess(r['label']['label_available_at'],fold['fit_at'])
        self.assertTrue(all(r['target_period']<'2026-06-30' for r in result['predictions']))
        self.assertEqual(result['heldout_samples'],9)

    def test_calibration_needs_samples_and_distinct_quarters(self):
        past=[dict(target_period='2024-03-31',actual_scaled=1,point_scaled=0) for _ in range(50)]
        self.assertIsNone(interval(0,past,EvaluationPolicy())['lower'])
        for i,r in enumerate(past):r['target_period']=('2023-03-31','2023-06-30','2023-09-30','2023-12-31')[i%4]
        self.assertEqual(interval(0,past,EvaluationPolicy())['lower'],-1)

    def test_intervals_use_only_already_disclosed_errors(self):
        policy=EvaluationPolicy(min_calibration_samples=1,min_calibration_periods=1)
        result=evaluate(panel(),policy)
        for r in result['predictions']:
            past=[v for v in result['predictions'] if v['model']==r['model'] and v['target_period']<r['target_period']
                  and v['label_available_at']<r['prediction_at']]
            expected=interval(r['point_scaled'],past,policy)
            self.assertEqual((r['lower'],r['upper']),(expected['lower'],expected['upper']))

    def test_metrics_negative_profit_direction_and_interval(self):
        record=dict(actual=-10,point=-12,actual_scaled=-.1,point_scaled=-.12,year_ago=-20,lower=-.2,upper=0)
        m=metrics([record]);self.assertAlmostEqual(m['scaled_mae'],.02)
        self.assertEqual(m['direction_accuracy'],1);self.assertEqual(m['coverage_80'],1)

    def test_repeat_exact_and_retired_company_reported(self):
        rows=panel();a=evaluate(rows,retired_entities=['retired']);b=evaluate(rows,retired_entities=['retired'])
        self.assertEqual(a,b)
        m=a['models']['seasonal_naive']
        self.assertEqual(m['excluding_retired']['n']*3,m['overall']['n']*2)
        self.assertIn('retired',m['by_group']['retired'])

    def test_duplicate_samples_rejected(self):
        rows=panel()
        with self.assertRaisesRegex(ValueError,'重复'):evaluate(rows+[rows[0]])

    def test_fitting_receives_past_labels_only(self):
        seen=[]
        def fit(train):
            seen.append(train)
            return lambda row:dict(test_model=row['baselines']['seasonal_naive'])
        policy=EvaluationPolicy(min_training_samples=1,min_training_periods=1)
        result=evaluate(panel(),policy,predictor=fit)
        self.assertTrue(seen)
        for fold,train in zip([f for f in result['folds'] if f['learned_model_fitted']],seen):
            self.assertTrue(all(r['target_period']<fold['target_period'] for r in train))

    def test_oracle_is_better_but_rejected_by_formal_audit(self):
        report=evaluate(panel());records=report['predictions']
        ordinary=metrics(records)['scaled_mae'];self.assertGreater(ordinary,0)
        oracle=[dict(r,point=r['actual'],point_scaled=r['actual_scaled']) for r in records]
        self.assertEqual(metrics(oracle)['scaled_mae'],0)
        row=panel()[0];row['feature_evidence'].append(dict(available_at=row['label']['label_available_at']))
        with self.assertRaises(ValueError):evaluate([row])

    def test_registry_counts_unique_configs_and_failed_experiments(self):
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(LocalStore(root));rows=panel()
            a=evaluate_registered(repo,rows,data_version='test')
            self.assertEqual(a,evaluate_registered(repo,rows,data_version='test'))
            bad=copy.deepcopy(rows);bad[0]['feature_evidence'].append(dict(available_at='2099-01-01T00:00:00+08:00'))
            bad[0]['id']=digest(bad[0])
            with self.assertRaises(ValueError):evaluate_registered(repo,bad,data_version='injected')
            self.assertEqual(counts(repo),dict(experiments=2,configurations=1,failures=1))


if __name__=='__main__':unittest.main()
