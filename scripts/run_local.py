"""Cross-platform launcher. Prefer invoking with Python -X utf8."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='观澜本地工作台')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--auto-refresh',action='store_true')
    args=parser.parse_args()
    if args.auto_refresh:os.environ['FINANCE_AUTO_REFRESH']='1'
    import uvicorn
    uvicorn.run('app.server:app',host='127.0.0.1',port=args.port)
