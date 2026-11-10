"""Check delivered file hashes; added runtime files are intentionally ignored."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    manifest=json.loads((ROOT/'delivery-manifest.json').read_text(encoding='utf-8'))
    errors=[]
    for i,item in enumerate(manifest['files'],1):
        path=ROOT/item['path']
        if not path.is_file():errors.append(dict(path=item['path'],error='missing'));continue
        digest=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
        if digest.hexdigest()!=item['sha256']:errors.append(dict(path=item['path'],error='hash mismatch'))
        if i%1000==0:print(f'Checked {i}/{len(manifest["files"])}',flush=True)
    print(json.dumps(dict(checked=len(manifest['files']),errors=errors),ensure_ascii=False,indent=2))
    return bool(errors)


if __name__=='__main__':sys.exit(main())
