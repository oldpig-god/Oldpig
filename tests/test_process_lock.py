import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from app import process_lock


class ProcessLockTests(unittest.TestCase):
    def test_another_process_cannot_acquire_until_owner_closes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'进程 lock'
            script="from app.process_lock import acquire; import sys; f=open(sys.argv[1],'a+b');\ntry: acquire(f)\nexcept BlockingIOError: sys.exit(17)"
            with path.open('a+b') as f:
                process_lock.acquire(f)
                result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True)
                self.assertEqual(result.returncode,17,result.stderr)
            result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
