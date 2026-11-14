"""Append-only evidence ledger on the existing JSON store contract."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
from .schema import DataError, canonical, digest, identity

PREFIX = 'earnings/'

class LocalStore:
    """Atomic create-only files; also works on Windows without hard links."""
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        if not key.startswith(PREFIX) or any(p in ('', '.', '..') for p in key.split('/')):
            raise DataError('数据路径格式不符')
        return self.root / key

    def create_json(self, key, value):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = gzip.compress(canonical(value), mtime=0)
        # An atomic rename publishes only a completed file. Exclusive lock serializes writers.
        lock = path.with_suffix('.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise DataError('同一记录正在写入，请重试')
        try:
            os.close(fd)
            if path.exists():
                return False
            temp = path.with_suffix('.tmp')
            temp.write_bytes(content)
            os.replace(temp, path)
            return True
        finally:
            lock.unlink()

    def get_json(self, key, optional=False):
        path = self._path(key)
        if optional and not path.exists():
            return None
        return json.loads(gzip.decompress(path.read_bytes()))

    def list_keys(self, prefix):
        return sorted(str(p.relative_to(self.root)).replace(os.sep, '/')
            for p in (self.root/prefix).rglob('*.json.gz'))

class Repository:
    def __init__(self, store):
        self.store = store

    def append(self, key, value):
        if self.store.create_json(key, value):
            return True
        if self.store.get_json(key) != value:
            raise DataError('不可变记录内容冲突：' + key)
        return False

    def save_document(self, metadata, content):
        """One file hash can have several dated publication receipts."""
        content_hash = hashlib.sha256(content).hexdigest()
        raw_key = PREFIX+'raw/'+content_hash+'.json.gz'
        self.append(raw_key, dict(sha256=content_hash, media_type=metadata['media_type'],
            content_base64=base64.b64encode(content).decode()))
        stable = {k:metadata[k] for k in ('source_id','entity_id','announcement_id','published_at','time_precision','source_url','media_type')}
        identity_hash = digest(dict(metadata=stable, content_hash=content_hash))
        key = PREFIX+'documents/'+identity(metadata['entity_id'])+'/'+identity_hash+'.json.gz'
        old = self.store.get_json(key, optional=True)
        document = dict(metadata, id=identity_hash, content_hash=content_hash, raw_key=raw_key)
        if old:
            return old
        publication=PREFIX+'publications/'+identity(metadata['source_id'])+'/'+identity(metadata['announcement_id'])+'/'
        previous=[self.store.get_json(k) for k in self.store.list_keys(publication)]
        document['prior_documents']=[r['document_id'] for r in previous if r['content_hash']!=content_hash]
        self.append(key, document)
        self.append(publication+content_hash+'.json.gz',dict(document_id=document['id'],content_hash=content_hash))
        return document

    def content(self, document):
        raw = self.store.get_json(document['raw_key'])
        content = base64.b64decode(raw['content_base64'], validate=True)
        if hashlib.sha256(content).hexdigest() != document['content_hash']:
            raise DataError('原文哈希不符')
        return content

    def save_event(self, event, pages):
        """Each numeric field links to an exact cell and surrounding PDF text."""
        if digest({k:v for k,v in event.items() if k!='id'}) != event['id']:
            raise DataError('事件内容与标识不符')
        from .pit import availability
        availability(event)
        document = self.store.get_json(PREFIX+'documents/'+identity(event['entity_id'])+'/'+event['document_id']+'.json.gz')
        for event_key,doc_key in [('entity_id','entity_id'),('symbol','symbol'),('source_id','source_id'),('version_published_at','published_at'),('ingested_at','ingested_at')]:
            if event[event_key]!=document[doc_key]:raise DataError('事件时间或身份与原始公告不一致')
        if document.get('prior_documents') and event['provenance']=='reconstructed_pit':
            raise DataError('同一公告原文变化，需要按新观测版本入库')
        if event['content_hash'] != document['content_hash']:
            raise DataError('事件原文哈希不符')
        for item in event['facts'].values():
            proof = item['proof']
            text = pages[proof['page']-1]
            quote = proof['quote']
            if proof.get('label_quote') and proof['label_quote'] not in pages[proof['label_page']-1]:
                raise DataError('字段名称未匹配原文')
            if quote not in text or proof['raw_value'] not in quote:
                raise DataError('财务数字未匹配原文单元格')
        key = PREFIX+'events/'+identity(event['entity_id'])+'/'+event['report_period']+'/'+event['id']+'.json.gz'
        return self.append(key, event)

    def events(self, entity_id):
        prefix = PREFIX+'events/'+identity(entity_id)+'/'
        withdrawn={self.store.get_json(k)['event_id'] for k in self.store.list_keys(PREFIX+'quality/'+identity(entity_id)+'/')}
        events = [self.store.get_json(k) for k in self.store.list_keys(prefix)]
        events=[e for e in events if e['id'] not in withdrawn]
        return sorted(events, key=lambda e:(e['available_at'], e['id']))

    def withdraw(self,entity_id,event_id,reason):
        record=dict(entity_id=identity(entity_id),event_id=identity(event_id),reason=reason)
        record['id']=digest(record)
        self.append(PREFIX+'quality/'+entity_id+'/'+record['id']+'.json.gz',record)
        return record

    def save_snapshot(self, snapshot):
        key = PREFIX+'features/'+identity(snapshot['entity_id'])+'/'+snapshot['id']+'.json.gz'
        self.append(key, snapshot)
        return key
