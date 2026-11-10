"""Offline portability tests. Fixtures are synthetic, not market data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd

BASE = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


data = load('shared_data', BASE / 'stock-data/scripts/collect.py')
quant = load('shared_quant', BASE / 'stock-quant/scripts/compute.py')
research = load('shared_research', BASE / 'stock-research/scripts/draft.py')


class PortableTests(unittest.TestCase):
    def test_symbols(self):
        self.assertEqual(data.symbol_parts('000858.SZ'), ('000858', 'SZ'))
        for value in ('600519.SZ', '../600519.SH', '600519'):
            with self.assertRaises(ValueError):
                data.symbol_parts(value)

    def test_announcements(self):
        df = pd.DataFrame({'period': ['2020-03-31'] * 3,
                           'ann': ['2020-04-01', '2021-04-01', None]})
        self.assertEqual(len(data.clean_dates(df, 'period', '2020-12-31', 'ann')), 1)

    def test_bad_ohlc(self):
        with self.assertRaises(ValueError):
            data.check_prices(pd.DataFrame({'date': ['2020-01-01'],
                'open': [2], 'high': [1], 'low': [1], 'close': [2]}))

    def test_rsi_boundaries(self):
        for prices, expected in [([1] * 30, 50), (list(range(1, 31)), 100),
                                  (list(range(30, 0, -1)), 0)]:
            result = quant.indicators(pd.DataFrame({
                'date': pd.date_range('2020-01-01', periods=30), 'close': prices}))
            self.assertEqual(result.RSI14.iloc[-1], expected)

    def test_correlation_intervals(self):
        a = pd.DataFrame({'date': pd.date_range('2020-01-01', periods=6),
                          'close': [1, 2, 2, 3, 2, 4]})
        self.assertEqual(quant.correlation(a, a.drop(index=2))['observations'], 3)

    def test_sibling_import_and_draft(self):
        # Use a known writable working directory, including sandboxed Windows.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            b = Path(tmp)
            (b / 'raw').mkdir()
            (b / 'bundle.json').write_text(json.dumps({'symbol': '600001.SH',
                'name': '合成测试样本', 'asof': '2025-12-31', 'start': '2024-01-01',
                'latest_price_date': '2025-12-31'}), encoding='utf-8')
            pd.DataFrame({'period': ['2025-12-31'], 'revenue': [100], 'parent_profit': [10],
                'net_profit': [12], 'cogs': [50], 'cfo': [15], 'roe_pct': [10]}).to_csv(b / 'financial.csv', index=False)
            pd.DataFrame({'数据日期': ['2025-12-31'], '总股本': [10],
                'PE(TTM)': [20], '市净率': [2]}).to_csv(b / 'raw/valuation.csv', index=False)
            prices = pd.DataFrame({'date': pd.date_range('2025-01-01', periods=30), 'close': range(1, 31)})
            prices.to_csv(b / 'price_raw.csv', index=False)
            prices.to_csv(b / 'price_qfq.csv', index=False)
            result = research.run(b)
            self.assertEqual(result['name'], '合成测试样本')
            self.assertTrue((b / 'quant/metrics.json').exists())
            self.assertTrue((b / 'research/研究底稿.md').exists())
            with self.assertRaises(FileExistsError):
                research.run(b)


if __name__ == '__main__':
    unittest.main()
