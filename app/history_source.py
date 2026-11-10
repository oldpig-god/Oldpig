"""Bounded daily HTTP adapter using the same documented Eastmoney kline fields as AKShare."""
import requests

FIELDS=['日期','开盘','收盘','最高','最低','成交量','成交额','振幅','涨跌幅','涨跌额','换手率']

def fetch(item,params):
    market=1 if item['exchange']=='SH' else 0
    query={'secid':str(market)+'.'+item['code'],'klt':'101','fqt':'0',
           'beg':params['start_date'],'end':params['end_date'],
           'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
    response=requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get',params=query,timeout=(5,20))
    response.raise_for_status();payload=response.json()
    if payload.get('rc',0)!=0:raise ValueError('Source response error: '+str(payload.get('rc')))
    data=payload.get('data')
    if data is None:return []
    if str(data.get('code'))!=item['code'] or data.get('market')!=market:raise ValueError('Source symbol/market mismatch')
    rows=[]
    for line in data.get('klines',[]):
        cells=line.split(',')
        if len(cells)!=len(FIELDS):raise ValueError('Unexpected source schema')
        rows.append(dict(zip(FIELDS,cells)))
    return rows
