"""Join downloaded GitHub release parts and verify each part plus whole ZIP.

Windows/Linux/macOS; Python standard library only, no project dependencies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def join(parts_dir, output, manifest):
    if output.exists():raise FileExistsError('Output already exists; not overwriting: '+str(output))
    if not output.parent.is_dir():raise FileNotFoundError(output.parent)
    for part in manifest['parts']:
        path=parts_dir/part['name']
        if not path.is_file() or path.stat().st_size!=part['bytes']:
            raise ValueError('Missing/incomplete part: '+str(path))
    temporary=output.with_name(output.name+'.joining')
    if temporary.exists():raise FileExistsError(temporary)
    whole=hashlib.sha256()
    try:
        with temporary.open('xb') as target:
            for part in manifest['parts']:
                digest=hashlib.sha256()
                with (parts_dir/part['name']).open('rb') as source:
                    for chunk in iter(lambda:source.read(8*1024*1024),b''):
                        digest.update(chunk);whole.update(chunk);target.write(chunk)
                if digest.hexdigest()!=part['sha256']:raise ValueError('SHA-256 mismatch: '+part['name'])
                print('Verified: '+part['name'],flush=True)
        if whole.hexdigest()!=manifest['zip_sha256'] or temporary.stat().st_size!=manifest['zip_bytes']:
            raise ValueError('Combined ZIP verification failed')
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print('Ready to extract: '+str(output.resolve()),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=Path(__file__).resolve().parents[1]/'contracts/release-assets.json')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    join(args.parts_dir,args.output,manifest)


if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR: '+str(exc),file=sys.stderr);sys.exit(1)
