import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import technical, core

class SelectionTests(unittest.TestCase):
    def test_manifest_drives_single_stock_not_fixed_watchlist(self):
        root=technical.PORTABLE
        manifest=json.loads((root/'manifest.json').read_text())
        symbol='600031.SH'
        manifest['watchlist']={symbol:'三一重工'}
        manifest['assets']={symbol:'三一重工',technical.BENCHMARK:'沪深300ETF'}
        manifest['sources']=[s for s in manifest['sources'] if s['symbol'] in manifest['assets']]
        manifest['quotes']=[q for q in manifest['quotes'] if q['symbol']==symbol]
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)
            for source in manifest['sources']+manifest['quotes']:
                (target/source['file']).write_bytes((root/source['file']).read_bytes())
            (target/'manifest.json').write_text(json.dumps(manifest))
            result=technical.analyze_snapshot(target)
            self.assertEqual([a['symbol'] for a in result['assets']],[symbol])
            self.assertEqual(result['comovement']['pairs'],[])
            self.assertEqual(list(result['series']),[symbol])
            json.dumps(result,allow_nan=False)

    def test_no_common_returns_yields_null_not_invalid_json(self):
        manifest=json.loads((technical.PORTABLE/'manifest.json').read_text())
        frames,quality=technical.load_frames(technical.PORTABLE,manifest)
        for frame in frames.values():frame.iloc[:-1]=float('nan')
        with patch.object(technical,'load_frames',return_value=(frames,quality)):
            result=technical.analyze_snapshot(technical.PORTABLE)
        self.assertEqual(result['dates']['return_observations'],0)
        self.assertIsNone(result['dates']['recent60_start'])
        self.assertIsNone(result['comovement']['recent60']['600031.SH']['002463.SZ'])
        self.assertEqual(result['assets'][0]['trend'],'历史不足')
        json.dumps(result,allow_nan=False)

    def test_selection_resolution_and_limits(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(core,'DATA',Path(temp)):
            core.init()
            self.assertEqual(list(technical.resolve_watchlist(['600519','600519.SH'])),['600519.SH'])
            self.assertEqual(list(technical.resolve_watchlist(['920001'])),['920001.BJ'])
            for invalid in [[],['600519']*13,['../../etc/passwd'],['无此公司名称']]:
                with self.assertRaises(ValueError):technical.resolve_watchlist(invalid)
