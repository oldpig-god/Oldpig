"""Account-scoped, immutable experiment records shared by local and cloud routes."""
import gzip
import json
import os
import re
import secrets
import threading
import platform
from importlib.metadata import version
from collections import defaultdict
from pathlib import Path
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,Field,ConfigDict
from . import core,strategy,strategy_data

LOCKS=defaultdict(threading.Lock)

class ExperimentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol:str=Field(default='600031.SH',min_length=1,max_length=30)
    fast:int=Field(default=20,ge=5,le=100)
    slow:int=Field(default=60,ge=8,le=250)
    capital:float=Field(default=100000,ge=10000,le=100000000)
    commission:float=Field(default=.0003,ge=0,le=.01)
    minimum:float=Field(default=5,ge=0,le=100)
    stamp:float=Field(default=.0005,ge=0,le=.01)
    historical_stamp:bool=True
    transfer:float=Field(default=.00001,ge=0,le=.01)
    slippage:float=Field(default=.0005,ge=0,le=.01)
    execution:str='open'
    train_fraction:float=Field(default=.7,ge=.5,le=.8)
    risk_free:float=Field(default=0,ge=0,le=.2)
    seed:int=Field(default=20261112,ge=0,lt=2**32)
    random_runs:int=Field(default=100,ge=20,le=200)

class LocalStore:
    def get_json(self,key,optional=False):
        path=core.DATA/'strategy'/key
        if optional and not path.exists():return None
        return json.loads(gzip.decompress(path.read_bytes()))
    def put_json(self,key,value):
        path=core.DATA/'strategy'/key;path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.'+secrets.token_hex(8)+'.tmp')
        temp.write_bytes(gzip.compress(strategy_data.canonical(value),mtime=0));os.replace(temp,path)
    def list_keys(self,prefix):
        root=core.DATA/'strategy'
        return sorted((str(p.relative_to(root)) for p in (root/prefix).glob('*.json.gz')),reverse=True)


def identifier(value):
    if not re.fullmatch(r'\d{8}T\d{12}-[a-f0-9]{16}',value):raise HTTPException(400,'实验编号格式错误')
    return value


def prefix(user):return 'users/'+user+'/strategy/experiments/'


def saved(store,user,experiment_id):
    record=store.get_json(prefix(user)+identifier(experiment_id)+'.json.gz',optional=True)
    if record is None:raise HTTPException(404,'该账号没有此实验')
    return record


def brief(record):
    r=record['result']
    return dict(id=record['id'],created_at=record['created_at'],symbol=r['symbol'],name=r['name'],config=r['config'],result_hash=r['result_hash'],data_hash=r['data_hash'],status=r['status'])


def routes(store_factory,user_for,check_action,page=None):
    router=APIRouter()
    @router.get('/strategy')
    def strategy_page():return page() if page else FileResponse(core.ROOT/'app/static/strategy/index.html')
    @router.get('/api/strategy/experiments')
    def experiments(request:Request):
        store=store_factory();user=user_for(request)
        try:
            keys=store.list_keys(prefix(user));records=[brief(store.get_json(k)) for k in keys[:20]]
            return dict(records=records,completed_experiments=len(keys),parameter_evaluations=len(keys)*9,count_note='本账号已保存实验，每次固定9组形成期参数评估，含重复；不包含失败、外部或未保存试验。重放不新增试验。')
        except Exception:raise HTTPException(502,'实验目录暂不可用，请稍后重试')
    @router.post('/api/strategy/run')
    def create(request:Request,body:ExperimentRequest):
        check_action(request);user=user_for(request);lock=LOCKS['strategy/'+user]
        cfg_dict=body.model_dump();symbol=cfg_dict.pop('symbol')
        try:cfg=strategy.Config(**cfg_dict).checked()
        except ValueError as exc:raise HTTPException(400,str(exc))
        if not lock.acquire(False):raise HTTPException(409,'本账号正在运行实验，请稍后再试')
        try:
            bundle=strategy_data.acquire(symbol);result=strategy.run(bundle,cfg)
            experiment_id=core.now().strftime('%Y%m%dT%H%M%S%f')+'-'+secrets.token_hex(8)
            record=dict(id=experiment_id,created_at=core.iso(),data=bundle,result=result,
                        reproduction=dict(python=platform.python_version(),packages={name:version(name) for name in ('pandas','numpy','requests')},
                                          engine_sources={name:(Path(__file__).parent/name).read_text(encoding='utf-8') for name in ('strategy.py','strategy_data.py')}))
            store_factory().put_json(prefix(user)+experiment_id+'.json.gz',record)
            return dict(experiment=brief(record),result=result)
        except ValueError as exc:raise HTTPException(422,str(exc))
        except Exception:raise HTTPException(502,'行情读取或实验保存失败，已有实验保持不变；请稍后重试')
        finally:lock.release()
    @router.get('/api/strategy/experiments/{experiment_id}')
    def read(request:Request,experiment_id:str):
        record=saved(store_factory(),user_for(request),experiment_id)
        return dict(experiment=brief(record),result=record['result'])
    @router.get('/api/strategy/experiments/{experiment_id}/download')
    def download(request:Request,experiment_id:str):
        record=saved(store_factory(),user_for(request),experiment_id)
        return Response(strategy_data.canonical(record),media_type='application/json',headers={'Content-Disposition':'attachment; filename="strategy-'+record['id']+'.json"'})
    @router.post('/api/strategy/experiments/{experiment_id}/replay')
    def replay(request:Request,experiment_id:str):
        check_action(request);record=saved(store_factory(),user_for(request),experiment_id)
        old=record['result']
        if old['code_hash']!=strategy.code_hash():raise HTTPException(409,'引擎版本已改变，请使用下载记录中对应版本代码离线重放；不会覆盖旧实验')
        try:result=strategy.run(record['data'],strategy.Config(**old['config']))
        except ValueError as exc:raise HTTPException(422,str(exc))
        return dict(identical=result==old,result_hash=result['result_hash'],original_hash=old['result_hash'])
    return router
