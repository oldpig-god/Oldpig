import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('join_release',Path(__file__).resolve().parents[1]/'scripts/join_release.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class JoinReleaseTests(unittest.TestCase):
    def fixture(self,root):
        chunks=[b'first-part',b'second-part']
        parts=[]
        for i,data in enumerate(chunks):
            name=f'archive.part{i:03d}';(root/name).write_bytes(data)
            parts.append(dict(name=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        full=b''.join(chunks)
        return dict(parts=parts,zip_sha256=hashlib.sha256(full).hexdigest(),zip_bytes=len(full)),full

    def test_verified_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest,data=self.fixture(root)
            module.join(root,root/'combined.zip',manifest)
            self.assertEqual((root/'combined.zip').read_bytes(),data)

    def test_corrupt_part_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest,_=self.fixture(root)
            path=root/manifest['parts'][1]['name'];path.write_bytes(b'x'*path.stat().st_size)
            with self.assertRaises(ValueError):module.join(root,root/'combined.zip',manifest)
            self.assertFalse((root/'combined.zip').exists());self.assertFalse((root/'combined.zip.joining').exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest,_=self.fixture(root);output=root/'combined.zip';output.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):module.join(root,output,manifest)
            self.assertEqual(output.read_bytes(),b'keep')
