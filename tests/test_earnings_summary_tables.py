import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.earnings.summary_tables import extract_core_pdf,verify_cell


class SummaryGeometryTests(unittest.TestCase):
    def test_original_ytd_and_quarter_columns_and_tampered_cell(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/earnings-summary-layouts.json').read_text())
        fixtures.append(json.loads((Path(__file__).parent/'fixtures/earnings-half-year-layout.json').read_text()))
        for fixture in fixtures:
            pages=[]
            for i,text in enumerate(fixture['pages']):
                tables=[]
                for t in fixture['tables'].get(str(i),[]):
                    tables.append(SimpleNamespace(bbox=t['bbox'],rows=[SimpleNamespace(cells=r) for r in t['cells']],
                                                  extract=lambda rows=t['rows']:rows))
                pages.append(SimpleNamespace(rect=SimpleNamespace(width=600),get_text=lambda **kw:'单位：'+fixture.get('unit','元'),
                    get_textbox=lambda rect:fixture['quotes'][json.dumps(list(rect))],
                    find_tables=lambda tables=tables:SimpleNamespace(tables=tables)))
            class Document:
                def __enter__(self):return pages
                def __exit__(self,*args):pass
            with patch('fitz.open',return_value=Document()):
                p=extract_core_pdf(b'pdf',fixture['pages'],fixture['period'])
                self.assertEqual(p['raw_value'],fixture['expected'])
                if fixture['period'].endswith('-09-30'):
                    self.assertEqual(p['quarter_proof']['raw_value'],'1,116,150,641.76')
                else:
                    self.assertEqual(p['unit'],'万元')
                    self.assertNotIn('quarter_proof',p)
                verify_cell(b'pdf',p)
                with self.assertRaisesRegex(ValueError,'原始PDF'):verify_cell(b'pdf',dict(p,raw_value='74'))
