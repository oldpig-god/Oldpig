import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LightDoctorTests(unittest.TestCase):
    def test_no_database_is_required_or_created_for_light_diagnosis(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/'missing data'
            result=subprocess.run([sys.executable,'-X','utf8',str(root/'scripts/doctor.py'),'--technical-only'],
                cwd=root,env=dict(os.environ,FINANCE_DATA_DIR=str(data)),capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(result.stdout);self.assertEqual(report['errors'],[])
            self.assertEqual(report['technical_snapshot']['series'],21)
            self.assertFalse(data.exists())
