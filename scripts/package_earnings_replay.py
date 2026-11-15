"""Package normalized research inputs and the small offline runner; no account files."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.earnings.repository import LocalStore,Repository
from app.earnings.schema import canonical
from app.earnings.sync import ManifestStore


def main():
    parser=argparse.ArgumentParser(description='盈利研究专项重放包')
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--summary',default='research/earnings/benchmark-summary.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    repo=Repository(LocalStore(args.data_dir));summary=json.loads(Path(args.summary).read_text(encoding='utf-8'))
    registration=repo.store.get_json('earnings/experiments/registrations/'+summary['trials'][0]['experiment_id']+'.json.gz')
    keys=set(repo.store.list_keys('earnings/datasets/'))|set(repo.store.list_keys('earnings/experiments/'))
    versions={repo.store.get_json(k)['data_version'] for k in repo.store.list_keys('earnings/experiments/registrations/')}
    for version in versions:
        manifest=ManifestStore(repo.store,version).manifest
        keys.add('earnings/manifests/'+version+'.json.gz')
        keys.update(item['key'] for item in manifest['objects'])
    files=['app/cloud_store.py','requirements-earnings.txt','requirements-earnings-models.txt',
           'scripts/earnings_data.py','scripts/earnings_benchmark.py','docs-earnings-evaluation.md',
           'research/earnings/benchmark-validation.json','research/earnings/code-version-map.json']
    files += [str(p.relative_to(root)).replace('\\','/') for p in sorted((root/'app/earnings').glob('*.py'))]
    readme=f'''# 盈利研究专项重放包

研究数据与固定配置已装入本包。无需账户配置即可本地重放。原始PDF通过输入记录中的来源URL与SHA256追溯，二进制原件独立归档。

当前结论：分位树尚未显著胜过季节基准。最近两个季度保留作独立检验。

## Windows PowerShell

```powershell
$env:PYTHONUTF8="1"
py -3.11 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements-earnings.txt -r requirements-earnings-models.txt
.\\.venv\\Scripts\\python.exe scripts/earnings_benchmark.py --dataset {summary['sample_dataset_id']} --output benchmark-repeat.json
```

Linux/macOS 使用 `python3 -m venv .venv`，将解释器路径换为 `.venv/bin/python`。

数据版本：`{summary['data_version']}`。
算法源码见仓库提交 `{registration['code_version']}`；精确模块哈希记录于每份实验。依赖版本锁定。
原验收环境为 Linux/Python3.10；Windows 命令按 Python3.11 提供。

基准、两组树和五组训练标签置换共8套固定管线。重放会保存完整结果到 `data/earnings/experiments/`。
实验ID包含源码提交与Python版本；比较数值结果时同时查看报告哈希和指标。
更多说明见主仓库 `docs-earnings-evaluation.md`。
'''
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        archive.writestr('app/__init__.py','')
        archive.writestr('foragent.md',readme)
        archive.writestr('benchmark-summary.json',Path(args.summary).read_bytes())
        for name in files:archive.write(root/name,name)
        for key in sorted(keys):
            archive.writestr('data/'+key,gzip.compress(canonical(repo.store.get_json(key)),mtime=0))
    result=dict(path=str(output),objects=len(keys),bytes=output.stat().st_size,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest(),sample_dataset_id=summary['sample_dataset_id'])
    output.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
