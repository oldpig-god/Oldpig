import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from app import technical

ROOT=Path(__file__).resolve().parents[1]
SNAPSHOT=ROOT/'research/technical/snapshot'


class TechnicalSnapshotTests(unittest.TestCase):
    def test_saved_input_reproduces_six_asset_results(self):
        result=technical.analyze_snapshot(SNAPSHOT)
        saved=json.loads((ROOT/'app/static/technical/analysis.json').read_text(encoding='utf-8'))
        self.assertEqual(result['assets'],saved['assets'])
        self.assertEqual(result['comovement'],saved['comovement'])
        self.assertEqual(result['event_statistics'],saved['event_statistics'])
        self.assertEqual(result['series'],saved['series'])
        self.assertEqual(len(result['assets']),6)
        self.assertTrue(all(q['last']==result['expected_end'] and q['missing_sessions']==0 for q in result['quality']))

    def test_evidence_archive_contains_verified_input_bytes(self):
        import zipfile
        with zipfile.ZipFile(ROOT/'app/static/technical/evidence.zip') as z:
            manifest=json.loads(z.read('snapshot/manifest.json'))
            for source in manifest['sources']+manifest['quotes']:
                if source.get('file'):
                    self.assertEqual(hashlib.sha256(z.read('snapshot/'+source['file'])).hexdigest(),source['sha256'])

    def test_evidence_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'snapshot';shutil.copytree(SNAPSHOT,root)
            manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
            path=root/manifest['sources'][0]['file'];path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'checksum'):technical.analyze_snapshot(root)

    def test_csv_permission_error_preserves_visible_analysis(self):
        result=json.loads((ROOT/'app/static/technical/analysis.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp);(output/'analysis.json').write_bytes(b'previous visible result')
            with patch('pandas.DataFrame.to_csv',side_effect=PermissionError('CSV is open in another application')):
                with self.assertRaises(PermissionError):technical.publish(result,SNAPSHOT,output,portable=False)
            self.assertEqual((output/'analysis.json').read_bytes(),b'previous visible result')
