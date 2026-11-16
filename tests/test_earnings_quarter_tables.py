import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.earnings.quarter_tables import extract_tables


class TableGeometryTests(unittest.TestCase):
    def test_original_wrapped_and_cross_page_quarter_tables(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/earnings-quarter-layouts.json').read_text())
        for fixture in fixtures:
            pages=[]
            for i,text in enumerate(fixture['pages']):
                tables=[]
                for t in fixture['tables'].get(str(i),[]):
                    tables.append(SimpleNamespace(bbox=t['bbox'],rows=[SimpleNamespace(cells=r) for r in t['cells']],
                                                  extract=lambda rows=t['rows']:rows))
                pages.append(SimpleNamespace(rect=SimpleNamespace(width=600),
                    get_text=lambda **kw:'单位：'+fixture['unit'],
                    find_tables=lambda tables=tables:SimpleNamespace(tables=tables)))
            class Document:
                def __enter__(self):return pages
                def __exit__(self,*args):pass
            with self.subTest(announcement=fixture['announcement_id']),patch('fitz.open',return_value=Document()):
                result=extract_tables(b'pdf',fixture['pages'],fixture['period'])
                self.assertEqual(result[fixture['period']]['core_profit']['raw_value'],fixture['expected'])
