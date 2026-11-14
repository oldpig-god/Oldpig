"""Publish a complete, content-checked ledger manifest to CloudStore."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .repository import PREFIX, Repository
from .schema import SCHEMA, canonical, digest


def publish(source, destination, workers=4):
    keys=source.store.list_keys(PREFIX)
    keys=[k for k in keys if not k.startswith(PREFIX+'manifests/')]
    objects=[dict(key=k,sha256=digest(source.store.get_json(k))) for k in keys]
    order=['raw','documents','publications','events','quality','registry','universe','features','runs']
    counts=dict(created=0,existing=0)
    def transfer(item):
        value=source.store.get_json(item['key'])
        if digest(value)!=item['sha256']:raise ValueError('同步期间源记录发生变化')
        return destination.append(item['key'],value)
    for group in order:
        subset=[o for o in objects if o['key'].split('/')[1]==group]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for created in pool.map(transfer,subset):counts['created' if created else 'existing']+=1
    code={p.name:p.read_text(encoding='utf-8') for p in sorted(Path(__file__).parent.glob('*.py'))}
    manifest=dict(schema=SCHEMA,objects=objects,engine_hash=digest(code))
    manifest['id']=digest(manifest)
    destination.append(PREFIX+'manifests/'+manifest['id']+'.json.gz',manifest)
    return dict(manifest_id=manifest['id'],objects=len(objects),**counts)


class ManifestStore:
    """Read exactly the object versions named by an immutable dataset manifest."""
    def __init__(self,store,manifest_id):
        self.store=store
        self.manifest=store.get_json(PREFIX+'manifests/'+manifest_id+'.json.gz')
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
