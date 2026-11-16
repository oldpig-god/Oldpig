import unittest
from app.earnings.guidance import amount_range,asof,profit_metric,release_period
from app.earnings.guidance_eval import evaluate_guidance,summarize


class GuidanceTests(unittest.TestCase):
    def test_fixed_schedule_never_uses_future_guidance_or_known_targets(self):
        from test_earnings_evaluation import events
        es=events(begin=2024,end=2025)
        record=dict(id='forecast',target_period='2025-12-31',kind='guidance',
                    available_at='2026-01-10T00:00:00+08:00',
                    facts=dict(core_profit=dict(lower='500',upper='600',basis='ytd')))
        rows,excluded=evaluate_guidance(es,[record],start='2025-12-31',offsets=(1,15,100))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['offset_days'],15)
        self.assertEqual(rows[0]['point'],550)
        self.assertEqual(excluded['already_disclosed'],1)
        self.assertEqual(excluded['no_public_core_guidance'],1)
        self.assertEqual(summarize(rows)[0]['n'],1)
    def test_amount_units_loss_and_signed_ranges(self):
        self.assertEqual(amount_range('盈利：151.00亿元–161.00亿元')['lower'],'15100000000.00')
        self.assertEqual(amount_range('亏损：100万元至50万元')['lower'],'-1000000')
        self.assertEqual(amount_range('-100至-50','万元')['upper'],'-500000')
        self.assertEqual(amount_range('100-150','万元')['upper'],'1500000')
        self.assertIsNone(amount_range('同比增长：100%-150%','万元'))
        self.assertIsNone(amount_range('100'))

    def test_parent_and_core_cannot_be_substituted(self):
        self.assertEqual(profit_metric('归属于上市公司股东扣除非经常性损益后的净利润'),'core_profit')
        self.assertEqual(profit_metric('归属于母公司股东的净利润'),'parent_profit')
        self.assertIsNone(profit_metric('扣除非经常性损益后的净利润'))

    def test_title_period(self):
        self.assertEqual(release_period('2024年度业绩预告修正公告'),'2024-12-31')
        self.assertEqual(release_period('2025年半年度业绩快报'),'2025-06-30')
        self.assertIsNone(release_period('关于业绩说明会的公告'))

    def test_future_and_unparsed_corrections(self):
        first=dict(id='1',target_period='2025-12-31',kind='guidance',available_at='2026-01-10T00:00:00+08:00',
                   facts=dict(core_profit=dict(lower='10',upper='20',basis='ytd')))
        correction=dict(first,id='2',available_at='2026-01-20T00:00:00+08:00',facts={})
        self.assertEqual(asof([first,correction],'2025-12-31','2026-01-10T00:00:00+08:00'),{})
        self.assertEqual(asof([first,correction],'2025-12-31','2026-01-15T00:00:00+08:00')['guidance']['lower'],'10')
        self.assertEqual(asof([first,correction],'2025-12-31','2026-01-21T00:00:00+08:00'),{})
