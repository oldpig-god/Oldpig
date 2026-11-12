"""Offline replay of a downloaded experiment; never downloads new market data."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import strategy
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('record',type=Path)
args=parser.parse_args()
record=json.loads(args.record.read_text(encoding='utf-8'))
if record['result']['code_hash']!=strategy.code_hash():
    sys.exit('Engine source version differs. Use the archived source/version in reproduction.engine_sources; do not overwrite the saved experiment.')
result=strategy.run(record['data'],strategy.Config(**record['result']['config']))
identical=result==record['result']
print(json.dumps({'identical':identical,'result_hash':result['result_hash'],'original_hash':record['result']['result_hash']}))
sys.exit(0 if identical else 1)
