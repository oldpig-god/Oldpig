import copy
import tempfile
import unittest
from pathlib import Path
from app.earnings.schema import DataError, available_time, digest, make_event, money, stamp
from app.earnings.pit import first_disclosed_target, previous_period, quarter, select
from app.earnings.features import Policy, registry, snapshot
from app.earnings.repository import LocalStore, Repository
from app.earnings.pdf_extract import extract, reviewed_cells
from app.earnings.cninfo import report_period
from app.earnings.service import accounting_checks


def fixture(period='2025-03-31',published='2025-04-20',values=None,**options):
    values=values or {'core_profit':'20','assets':'1000','revenue':'100'}
    page='\n'.join(k+' '+str(v) for k,v in values.items())
    document=dict(id=digest({'p':period,'pub':published,'v':values}),entity_id='cninfo_test',symbol='600031.SH',
        name='测试公司',source_id='cninfo',content_hash='a'*64,announcement_id='123',
        media_type='application/pdf',published_at=published,time_precision='day',ingested_at='2026-11-14T09:00:00+08:00')
    facts={k:dict(raw_value=str(v),unit='元',page=1,quote=k+' '+str(v),column='current_ytd') for k,v in values.items()}
    return make_event(document,period,facts,scope_version=options.pop('scope_version','reviewed-v1'),**options)


class TemporalTests(unittest.TestCase):
    def test_day_precision_is_next_day_and_strict_before(self):
        e=fixture();self.assertEqual(select([e],'2025-04-20T23:59:59+08:00'),{})
        self.assertEqual(select([e],'2025-04-21T00:00:00+08:00'),{})
        self.assertIn('2025-03-31',select([e],'2025-04-21T08:30:00+08:00'))
    def test_timezone_required(self):
        with self.assertRaises(DataError):stamp('2025-04-21T08:30:00')
        self.assertEqual(stamp('2025-04-21T00:30:00Z').hour,8)
    def test_future_revision_does_not_replace_original(self):
        first=fixture();changed=fixture(published='2025-06-01',values={'core_profit':'99'},supersedes=first['id'],revision_kind='correction')
        self.assertEqual(select([first,changed],'2025-05-01T08:30:00+08:00')['2025-03-31']['id'],first['id'])
        self.assertEqual(select([first,changed],'2025-06-02T08:30:00+08:00')['2025-03-31']['id'],changed['id'])
    def test_latest_table_and_estimated_dates_are_not_pit(self):
        for evidence in ['latest_only','timestamp_estimated']:
            self.assertFalse(select([fixture(provenance=evidence)],'2026-11-15T08:30:00+08:00'))
    def test_observed_snapshot_not_backdated(self):
        e=fixture(provenance='observed_pit')
        self.assertFalse(select([e],'2025-05-01T08:30:00+08:00'))
        self.assertTrue(select([e],'2026-11-14T10:00:00+08:00'))
    def test_forged_availability_is_detected(self):
        e=fixture();e['available_at']='2024-01-01T00:00:00+08:00'
        with self.assertRaisesRegex(DataError,'公开证据'):select([e],'2025-01-01T08:30:00+08:00')
    def test_unlinked_versions_and_missing_ancestor(self):
        a=fixture();b=fixture(published='2025-05-01',values={'core_profit':'50'})
        with self.assertRaises(DataError):select([a,b],'2025-06-01T08:30:00+08:00')
        b=fixture(published='2025-05-01',supersedes=a['id'],revision_kind='correction')
        with self.assertRaises(DataError):select([b],'2025-06-01T08:30:00+08:00')
    def test_naive_second_precision_refused(self):
        with self.assertRaises(DataError):available_time('2025-01-01T12:00:00','second')


class AccountingTests(unittest.TestCase):
    def test_all_four_quarters_and_negative_profits(self):
        events=[fixture(p,pub,{'core_profit':v}) for p,pub,v in [
            ('2025-03-31','2025-04-20','20'),('2025-06-30','2025-08-20','60'),
            ('2025-09-30','2025-10-20','50'),('2025-12-31','2026-03-20','100')]]
        self.assertEqual([quarter(e,events[i-1] if i else None,'core_profit')['value'] for i,e in enumerate(events)],['20','40','-10','50'])
    def test_missing_previous_not_filled(self):
        e=fixture('2025-06-30','2025-08-20');q=quarter(e,None,'core_profit')
        self.assertIsNone(q['value']);self.assertIn('缺少',q['reason'])
    def test_scope_and_entity_changes_not_subtracted(self):
        a=fixture();b=fixture('2025-06-30','2025-08-20',scope_version='new-scope')
        self.assertIsNone(quarter(b,a,'core_profit')['value'])
        b=fixture('2025-06-30','2025-08-20');b['entity_id']='different'
        self.assertIsNone(quarter(b,a,'core_profit')['value'])
    def test_stock_value_not_differenced(self):
        with self.assertRaises(DataError):quarter(fixture(),None,'assets')
    def test_late_predecessor_correction_requires_compatible_current(self):
        a=fixture();b=fixture('2025-06-30','2025-08-20')
        revised=fixture(published='2025-09-01',supersedes=a['id'],revision_kind='correction')
        self.assertIsNone(quarter(b,revised,'core_profit')['value'])
    def test_first_disclosed_label_is_frozen(self):
        a=fixture();b=fixture('2025-06-30','2025-08-20',{'core_profit':'60'})
        revised=fixture('2025-06-30','2026-01-20',{'core_profit':'999'},supersedes=b['id'],revision_kind='correction')
        label=first_disclosed_target([a,b,revised],'2025-06-30')
        self.assertEqual(label['value'],'40');self.assertEqual(label['parents'],[b['id'],a['id']])
    def test_exact_money_units_and_zero(self):
        self.assertEqual(money('1,234.56','万元'),'12345600.00')
        self.assertEqual(money('0','千元'),'0');self.assertEqual(money('(12.3)','亿元'),'-1230000000.0')
        with self.assertRaises(DataError):money('NaN','元')
    def test_accounting_identity(self):
        e=fixture(values={'assets':'100','liabilities':'40','total_equity':'60','net_profit':'10','parent_profit':'9','minority_profit':'1'})
        self.assertEqual(accounting_checks(e),[]);e['facts']['total_equity']['value']='20'
        self.assertEqual(accounting_checks(e)[0]['check'],'assets=liabilities+equity')
    def test_preceding_period(self):
        self.assertEqual(previous_period('2025-03-31'),'2024-12-31')
        self.assertEqual(previous_period('2025-12-31'),'2025-09-30')


class FeatureTests(unittest.TestCase):
    def test_future_leak_feature_is_excluded(self):
        a=fixture();future=fixture('2025-06-30','2025-08-20',{'core_profit':'9999'})
        s=snapshot([a,future],'cninfo_test','2025-05-01T08:30:00+08:00','2025-06-30')
        self.assertEqual(s['features']['core_profit_quarter']['value'],'20')
        self.assertNotIn(future['id'],s['input_event_ids'])
    def test_repeat_identical_and_full_lineage(self):
        args=([fixture()],'cninfo_test','2025-05-01T08:30:00+08:00','2025-06-30')
        a=snapshot(*args);self.assertEqual(a,snapshot(*args));self.assertEqual(a['features']['core_profit_to_assets']['value'],'0.0200000000000000')
        self.assertTrue(a['features']['core_profit_quarter']['parents']);self.assertEqual(a['features']['core_profit_quarter']['unit'],'CNY')
    def test_zero_is_not_missing_and_staleness_configurable(self):
        a=fixture(values={'core_profit':'0','assets':'1000'})
        s=snapshot([a],'cninfo_test','2025-05-01T08:30:00+08:00','2025-06-30')
        self.assertEqual(s['features']['core_profit_quarter']['value'],'0');self.assertFalse(s['features']['core_profit_quarter']['missing'])
        s=snapshot([a],'cninfo_test','2025-05-01T08:30:00+08:00','2025-06-30',Policy(max_financial_age_days=10))
        self.assertTrue(s['features']['core_profit_quarter']['missing'])
    def test_no_history_and_target_already_disclosed(self):
        self.assertEqual(snapshot([],'cninfo_test','2025-05-01T08:30:00+08:00','2025-06-30')['status'],'no_history')
        a=fixture();self.assertEqual(snapshot([a],'cninfo_test','2025-05-01T08:30:00+08:00','2025-03-31')['status'],'target_disclosed')
    def test_registry_tracks_frequency_missing_source(self):
        for v in registry().values():
            for k in ['unit','source','publication','frequency','missing_policy','version']:self.assertIn(k,v)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.repo=Repository(LocalStore(self.temp.name))
    def doc(self,content=b'%PDF-original',captured='2026-11-14T09:00:00+08:00'):
        return self.repo.save_document(dict(source_id='cninfo',entity_id='cninfo_test',symbol='600031.SH',name='test',announcement_id='123',published_at='2025-04-20',time_precision='day',source_url='https://static.cninfo.com.cn/finalpage/test.PDF',media_type='application/pdf',ingested_at=captured),content)
    def test_document_repeat_and_original_bytes(self):
        a=self.doc();self.assertEqual(a,self.doc(captured='2026-11-15T09:00:00+08:00'));self.assertEqual(self.repo.content(a),b'%PDF-original')
    def test_append_conflict(self):
        self.repo.append('earnings/example/a.json.gz',{'value':1})
        self.assertFalse(self.repo.append('earnings/example/a.json.gz',{'value':1}))
        with self.assertRaises(DataError):self.repo.append('earnings/example/a.json.gz',{'value':2})
    def test_changed_original_is_not_backdated(self):
        a=self.doc();b=self.doc(b'%PDF-new');self.assertEqual(b['prior_documents'],[a['id']])
        f={'core_profit':dict(raw_value='10',unit='元',page=1,quote='profit 10',column='current_ytd')}
        with self.assertRaises(DataError):self.repo.save_event(make_event(b,'2025-03-31',f),['profit 10'])
    def test_proof_and_publication_match(self):
        d=self.doc();f={'core_profit':dict(raw_value='10',unit='元',page=1,quote='profit 10',column='current_ytd')}
        e=make_event(d,'2025-03-31',f);self.assertTrue(self.repo.save_event(e,['profit 10']));self.assertFalse(self.repo.save_event(e,['profit 10']))
        bad=copy.deepcopy(e);bad['version_published_at']='2024-01-01';bad['available_at']='2024-01-02T00:00:00+08:00';bad['id']=digest({k:v for k,v in bad.items() if k!='id'})
        with self.assertRaises(DataError):self.repo.save_event(bad,['profit 10'])
    def test_forged_quote_rejected(self):
        d=self.doc();e=make_event(d,'2025-03-31',{'core_profit':dict(raw_value='10',unit='元',page=1,quote='profit 10',column='current_ytd')})
        with self.assertRaises(DataError):self.repo.save_event(e,['not the original row'])


class PdfContractTests(unittest.TestCase):
    def test_notes_do_not_eat_the_first_digits(self):
        pages=['合并资产负债表\n单位：千元 币种：人民币\n货币资金 七、1      38,451,241      20,383,176\n资产总计   100,000    99,000']
        facts,_=extract(pages,'2025-12-31');self.assertEqual(facts['cash']['raw_value'],'38,451,241')
    def test_equity_heading_not_mistaken_for_total(self):
        pages=['合并资产负债表\n单位：千元 币种：人民币\n所有者权益（或股东权益）：\n实收资本 七、47  10,000  9,000\n所有者权益（或股东权益）合计 60,000 50,000']
        f,_=extract(pages,'2025-12-31');self.assertEqual(f['total_equity']['raw_value'],'60,000')
    def test_parent_statement_not_used_and_blank_not_zero(self):
        pages=['合并利润表\n单位：元 币种：人民币\n一、营业总收入      100     90\n销售费用       10\n母公司利润表\n销售费用      50    40']
        f,_=extract(pages,'2025-12-31');self.assertEqual(f['revenue']['raw_value'],'100');self.assertNotIn('selling_expense',f)
    def test_q3_summary_ytd_column(self):
        pages=['单位：千元 币种：人民币\n归属于上市公司股东的扣除非经常性损益的净利润 2,042,387 1,696,805 1,696,805 20.37 6,724,224 7,106,247 7,106,247 -5.38']
        f,_=extract(pages,'2026-09-30');self.assertEqual(f['core_profit']['raw_value'],'6,724,224')
    def test_reviewed_cell_and_title_filter(self):
        with self.assertRaises(DataError):reviewed_cells(['hello'],{'assets':dict(page=1,quote='missing 100',raw_value='100',unit='元',column='current')})
        self.assertEqual(report_period('测试2025年年度报告'),'2025-12-31');self.assertIsNone(report_period('测试2025年年度报告摘要'))


class UniverseTests(unittest.TestCase):
    def test_future_delisting_does_not_remove_historical_member(self):
        from app.earnings.universe import eligible_entities
        base=dict(entity_id='cninfo_test',published_at='2020-01-01',time_precision='day',ingested_at='2026-11-14T09:00:00+08:00',provenance='reconstructed_pit',value=True)
        listing=dict(base,field='listed',effective_at='2020-01-03T00:00:00+08:00')
        retired=dict(base,field='delisted',published_at='2025-06-01',effective_at='2025-06-10T00:00:00+08:00')
        self.assertEqual(eligible_entities([listing,retired],'2024-01-01T08:30:00+08:00'),['cninfo_test'])
        self.assertEqual(eligible_entities([listing,retired],'2025-06-11T08:30:00+08:00'),[])
    def test_snapshot_inventory_not_backdated_to_listing(self):
        from app.earnings.universe import eligible_entities
        listing=dict(entity_id='cninfo_test',field='listed',value=True,published_at='2020-01-01',time_precision='day',effective_at='2020-01-03T00:00:00+08:00',ingested_at='2026-11-14T09:00:00+08:00',provenance='observed_pit')
        self.assertEqual(eligible_entities([listing],'2024-01-01T08:30:00+08:00'),[])


class LayoutRegressionTests(unittest.TestCase):
    def test_annual_value_not_first_quarter(self):
        pages=['主要会计数据\n营业收入（元） 1000 800 25 700\n归属于上市公司股东的扣除非经常性\n 300 200 50 100\n损益的净利润（元）',
            '分季度主要财务指标\n单位：元\n第一季度 第二季度 第三季度 第四季度\n归属于上市公司股东的扣除非经常性损益的净利润\n 10 20 30 40']
        facts,_=extract(pages,'2025-12-31');self.assertEqual(facts['core_profit']['raw_value'],'300')
    def test_dated_headings_and_chinese_note_numbers(self):
        pages=['2025年12月31日合并资产负债表\n（除特别注明外，金额单位均为人民币元）\n资产总计  1,000 900\n2025年度合并利润表\n（除特别注明外，金额单位均为人民币元）\n销售费用 五、（三十八）   120 100']
        facts,_=extract(pages,'2025-12-31');self.assertEqual(facts['assets']['raw_value'],'1,000');self.assertEqual(facts['selling_expense']['raw_value'],'120')
    def test_parentheses_retain_negative_amount(self):
        pages=['合并利润表\n单位：元 币种：人民币\n五、净利润  (100) 90']
        facts,_=extract(pages,'2025-12-31');self.assertEqual(money(facts['net_profit']['raw_value'],'元'),'-100')


class BlankRowRegressionTests(unittest.TestCase):
    def test_blank_minority_not_next_income_line(self):
        pages=['合并利润表\n单位：元 币种：人民币\n2.少数股东损益\n六、其他综合收益的税后净额    19,087,135 -40,282,448']
        facts,_=extract(pages,'2025-03-31');self.assertNotIn('minority_profit',facts)
    def test_split_deducted_label_before_and_after_value(self):
        pages=['单位：千元 币种：人民币\n归属于上市公司股东的扣\n1,696,805 1,502,002 1,502,002 12.97 7,106,247 4,627,834 4,627,834 53.55\n除非经常性损益的净利润']
        facts,_=extract(pages,'2025-09-30');self.assertEqual(facts['core_profit']['raw_value'],'7,106,247')


class IngestionTests(unittest.TestCase):
    def test_collect_original_and_correction_is_idempotent(self):
        from unittest.mock import patch
        from app.earnings.service import collect
        from datetime import datetime
        from app.earnings.schema import SH
        def item(identifier,day,title):
            return dict(secCode='600031',secName='测试公司',orgId='test',announcementId=identifier,
                announcementTitle=title,announcementTime=int(datetime.fromisoformat(day).replace(tzinfo=SH).timestamp()*1000),adjunctUrl='finalpage/'+day+'/'+identifier+'.PDF')
        class Source:
            def announcements(self,*args):return dict(orgId='test',zwjc='测试公司'),[
                item('1','2025-04-20','2025年第一季度报告'),item('2','2025-06-20','2025年第一季度报告（更正后）')]
            def download(self,a):return 'https://static.cninfo.com.cn/'+a['adjunctUrl'],b'%PDF-'+a['announcementId'].encode()
        pages=['证券代码：600031\n单位：元 币种：人民币\n合并资产负债表\n单位：元 币种：人民币\n资产总计 1000 900\n合并利润表\n单位：元 币种：人民币\n一、营业总收入 100 90\n归属于上市公司股东的扣除非经常性损益的净利润 20 18 10']
        with tempfile.TemporaryDirectory() as temp:
            r=Repository(LocalStore(temp))
            with patch('app.earnings.service.pages_from_pdf',return_value=pages):
                first=collect(r,'600031.SH','2025-01-01','2026-01-01',source=Source())
                again=collect(r,'600031.SH','2025-01-01','2026-01-01',source=Source())
            self.assertEqual([i['status'] for i in first['outcomes']],['created','created'])
            self.assertEqual([i['status'] for i in again['outcomes']],['existing','existing'])
            events=r.events('cninfo_test');self.assertEqual(len(events),2)
            correction=next(e for e in events if e['revision_kind']=='correction')
            self.assertEqual(correction['published_at'],'2025-04-20')
            self.assertEqual(correction['version_published_at'],'2025-06-20')
            self.assertEqual(select(events,'2025-05-01T08:30:00+08:00')['2025-03-31']['revision_kind'],'initial')
    def test_quality_withdrawal_preserves_raw_and_event(self):
        with tempfile.TemporaryDirectory() as temp:
            r=Repository(LocalStore(temp));e=fixture()
            key='earnings/events/'+e['entity_id']+'/'+e['report_period']+'/'+e['id']+'.json.gz'
            r.append(key,e);r.withdraw(e['entity_id'],e['id'],'field extraction superseded')
            self.assertEqual(r.events(e['entity_id']),[]);self.assertEqual(r.store.get_json(key),e)


class PublishTests(unittest.TestCase):
    def test_publish_replay_keeps_same_manifest(self):
        from app.earnings.sync import publish
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            source=Repository(LocalStore(a));destination=Repository(LocalStore(b))
            source.append('earnings/events/entity/2025-03-31/test.json.gz',{'v':1})
            first=publish(source,destination);again=publish(source,destination)
            self.assertEqual(first['manifest_id'],again['manifest_id']);self.assertEqual(first['created'],1);self.assertEqual(again['existing'],1)
            manifest=destination.store.get_json('earnings/manifests/'+first['manifest_id']+'.json.gz')
            self.assertEqual(manifest['objects'][0]['sha256'],digest({'v':1}))


class SplitCurrencyRegressionTests(unittest.TestCase):
    def test_retired_company_split_profit_and_unit(self):
        pages=['营业收入（元） 28,171,813.23 29,259,137.10 -3.72%\n归属于上市公司股东的扣除\n非经常性损益的净利润 -2,858,879.15 1,514,332.18 -2.89%\n（元）']
        f,_=extract(pages,'2023-03-31');self.assertEqual(money(f['core_profit']['raw_value'],f['core_profit']['unit']),'-2858879.15')


class FrozenManifestTests(unittest.TestCase):
    def test_later_quality_review_does_not_change_old_dataset(self):
        from app.earnings.sync import publish,ManifestStore
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            source=Repository(LocalStore(a));destination=Repository(LocalStore(b));e=fixture()
            source.append('earnings/events/'+e['entity_id']+'/'+e['report_period']+'/'+e['id']+'.json.gz',e)
            first=publish(source,destination);destination.withdraw(e['entity_id'],e['id'],'later extraction review')
            self.assertEqual(destination.events(e['entity_id']),[])
            frozen=Repository(ManifestStore(destination.store,first['manifest_id']))
            self.assertEqual(frozen.events(e['entity_id']),[e])
