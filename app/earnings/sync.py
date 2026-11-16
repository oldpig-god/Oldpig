"""Publish a complete, content-checked ledger manifest to CloudStore."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import requests
from .repository import PREFIX, Repository,logical_store
from .schema import SCHEMA, canonical, digest


FINANCIAL_INPUT_GROUPS={'documents','publications','events','quality','registry','universe','quarter_cells','guidance','runs','panels'}


def make_manifest(source,groups=None):
    keys=source.store.list_keys(PREFIX)
    keys=[k for k in keys if not k.startswith((PREFIX+'manifests/',PREFIX+'transfers/'))]
    if groups is not None:keys=[k for k in keys if k.split('/')[1] in groups]
    objects=[dict(key=k,sha256=digest(source.store.get_json(k))) for k in keys]
    code={p.name:p.read_text(encoding='utf-8') for p in sorted(Path(__file__).parent.glob('*.py'))}
    manifest=dict(schema=SCHEMA,objects=objects,engine_hash=digest(code))
    if groups is not None:
        manifest.update(included_groups=sorted(groups),raw_evidence='original_source_url_and_sha256; binary_archive_separate')
    manifest['id']=digest(manifest)
    return manifest


def freeze(repository,financial_inputs=False):
    manifest=make_manifest(repository,FINANCIAL_INPUT_GROUPS if financial_inputs else None)
    repository.append(PREFIX+'manifests/'+manifest['id']+'.json.gz',manifest)
    return dict(manifest_id=manifest['id'],objects=len(manifest['objects']),engine_hash=manifest['engine_hash'])


def publish(source, destination, workers=4,manifest_id=None,checkpoint_scope=None,attempts=3,retry_delay=1):
    manifest=(ManifestStore(source.store,manifest_id).manifest if manifest_id else make_manifest(source))
    objects=manifest['objects']
    keys=[o['key'] for o in objects]
    order=['raw','documents','publications','events','quality','registry','universe','features',
           'quarter_cells','guidance','samples','datasets','exclusions','experiments','predictions','panels','runs']
    unknown={k.split('/')[1] for k in keys}-set(order)
    if unknown:raise ValueError('未登记的数据类型：'+','.join(sorted(unknown)))
    counts=dict(created=0,existing=0)
    journal_prefix=PREFIX+'transfers/'+digest(checkpoint_scope)+'/' if checkpoint_scope else None
    completed=set(source.store.list_keys(journal_prefix)) if journal_prefix else set()
    existing=set(destination.store.list_keys(PREFIX))
    verified={}
    for key in sorted(k for k in existing if k.startswith(PREFIX+'manifests/')):
        prior=ManifestStore(destination.store,key.rsplit('/',1)[-1].split('.')[0]).manifest
        verified.update({o['key']:o['sha256'] for o in prior['objects']})
    def transfer(item):
        receipt=journal_prefix+digest(item)+'.json.gz' if journal_prefix else None
        if receipt in completed and item['key'] in existing:return False
        if item['key'] in existing and verified.get(item['key'])==item['sha256']:
            return False
        value=source.store.get_json(item['key'])
        if digest(value)!=item['sha256']:raise ValueError('同步期间源记录发生变化')
        for attempt in range(attempts):
            try:
                created=destination.append(item['key'],value)
                break
            except (requests.ConnectionError,requests.Timeout):
                if attempt+1==attempts:raise
                time.sleep(retry_delay*2**attempt)
        if receipt:source.append(receipt,dict(destination=digest(checkpoint_scope),**item))
        return created
    for group in order:
        subset=[o for o in objects if o['key'].split('/')[1]==group]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for created in pool.map(transfer,subset):counts['created' if created else 'existing']+=1
    destination.append(PREFIX+'manifests/'+manifest['id']+'.json.gz',manifest)
    return dict(manifest_id=manifest['id'],objects=len(objects),**counts)


class ManifestStore:
    """Read exactly the object versions named by an immutable dataset manifest."""
    def __init__(self,store,manifest_id):
        self.store=logical_store(store)
        self.manifest=self.store.get_json(PREFIX+'manifests/'+manifest_id+'.json.gz')
        if digest({k:v for k,v in self.manifest.items() if k!='id'})!=manifest_id:
            raise ValueError('数据清单内容与版本标识不一致')
        self.objects={o['key']:o['sha256'] for o in self.manifest['objects']}

    def get_json(self,key,optional=False):
        if key not in self.objects:
            if optional:return None
            raise KeyError(key)
        value=self.store.get_json(key)
        if digest(value)!=self.objects[key]:raise ValueError('数据版本哈希不符')
        return value

    def list_keys(self,prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))
