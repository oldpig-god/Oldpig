import base64
import gzip
import tempfile
import unittest
from collections import Counter
from unittest.mock import Mock,patch
import requests
from pathlib import Path

from app.earnings.repository import LocalStore,Repository,SegmentedStore
from app.earnings.schema import DataError,canonical,digest
from app.earnings.sync import ManifestStore,publish


class SegmentedOriginalTests(unittest.TestCase):
    def test_bulk_upload_timeout_is_explicit(self):
        from app.cloud_store import CloudStore
        response=Mock(ok=True)
        with patch('app.cloud_store.requests.post',return_value=response) as post:
            store=CloudStore(env='test',key='test',upload_timeout=300)
            self.assertTrue(store.create_json('earnings/test.json.gz',{}))
            self.assertEqual(post.call_args.kwargs['timeout'][0],300)
    def test_large_original_reads_like_a_single_object(self):
        with tempfile.TemporaryDirectory() as root:
            base=LocalStore(root);store=SegmentedStore(base,inline_chars=20,part_chars=12)
            repo=Repository(store)
            value=dict(sha256='example',media_type='application/pdf',content_base64=base64.b64encode(b'%PDF-'+b'example'*30).decode())
            key='earnings/raw/example.json.gz'
            self.assertTrue(repo.append(key,value));self.assertFalse(repo.append(key,value))
            self.assertEqual(repo.store.get_json(key),value)
            self.assertEqual(repo.store.list_keys('earnings/'),[key])
            self.assertGreater(len(base.list_keys('earnings/blob_parts/')),1)
            descriptor=base.get_json(key)
            self.assertEqual(descriptor['storage_encoding'],'earnings-base64-parts-v1')

    def test_part_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            base=LocalStore(root);store=SegmentedStore(base,inline_chars=10,part_chars=8)
            store.create_json('earnings/raw/test.json.gz',dict(content_base64='a'*40))
            key=base.list_keys('earnings/blob_parts/')[0]
            (Path(root)/key).write_bytes(gzip.compress(canonical(dict(text='tampered'))))
            with self.assertRaisesRegex(DataError,'分块哈希'):store.get_json('earnings/raw/test.json.gz')

    def test_inline_local_and_segmented_cloud_share_same_frozen_manifest(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            original=LocalStore(a);physical=LocalStore(b)
            source=Repository(original)
            destination=Repository(SegmentedStore(physical,inline_chars=10,part_chars=8))
            key='earnings/raw/test.json.gz';value=dict(content_base64=base64.b64encode(b'%PDF-'+b'abc'*20).decode())
            source.append(key,value)
            result=publish(source,destination)
            frozen=ManifestStore(physical,result['manifest_id'])
            self.assertEqual(frozen.get_json(key),source.store.get_json(key))
            self.assertEqual(publish(source,destination)['manifest_id'],result['manifest_id'])
            self.assertEqual(result['objects'],1)

    def test_manifest_itself_is_content_checked(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            store.create_json('earnings/manifests/wrong.json.gz',dict(id='wrong',objects=[],engine_hash='test'))
            with self.assertRaisesRegex(ValueError,'清单内容'):ManifestStore(store,'wrong')

    def test_sync_resumes_after_a_transport_failure_without_resending_completed_objects(self):
        class Destination:
            def __init__(self):self.values={};self.calls=Counter();self.fail=True
            def create_json(self,key,value):
                self.calls[key]+=1
                if key.endswith('/b.json.gz') and self.fail:raise requests.ConnectionError('test interruption')
                if key in self.values:return False
                self.values[key]=value;return True
            def get_json(self,key,optional=False):return self.values.get(key) if optional else self.values[key]
            def list_keys(self,prefix):return sorted(k for k in self.values if k.startswith(prefix))
        with tempfile.TemporaryDirectory() as root:
            source=Repository(LocalStore(root));storage=Destination();target=Repository(storage)
            for name in ('a','b'):source.append('earnings/events/'+name+'.json.gz',dict(value=name))
            with self.assertRaises(requests.ConnectionError):
                publish(source,target,workers=1,checkpoint_scope='test-destination',retry_delay=0)
            self.assertEqual(storage.calls['earnings/events/b.json.gz'],3)
            self.assertEqual(storage.list_keys('earnings/manifests/'),[])
            storage.fail=False
            result=publish(source,target,workers=1,checkpoint_scope='test-destination',retry_delay=0)
            self.assertEqual(storage.calls['earnings/events/a.json.gz'],1)
            self.assertEqual((result['created'],result['existing'],result['objects']),(1,1,2))


if __name__=='__main__':unittest.main()
