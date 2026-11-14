"""THS industry source, following AKShare's public catalog and annual index APIs.
No Eastmoney code mapping: identities, catalog and histories stay in one taxonomy.
"""
import hashlib,json,math,re,threading,time
from html.parser import HTMLParser
from pathlib import Path
import requests
from . import core
SOURCE='同花顺行业指数（AKShare 同源接口）'
DOC='https://akshare.akfamily.xyz/data/stock/stock_board.html'
PREFIX='system/industry-ths/'
NETWORK=threading.BoundedSemaphore(2)
TOKEN_LOCK=threading.Lock()
TOKEN=(0,None)
JS=Path(__file__).parent/'vendor/akshare-ths.js'

def headers():
    global TOKEN
    with TOKEN_LOCK:
        if time.monotonic()-TOKEN[0]>30 or not TOKEN[1]:
            from py_mini_racer import py_mini_racer
            ctx=py_mini_racer.MiniRacer();ctx.eval(JS.read_text(),timeout=1000,max_memory=32*1024*1024)
            token=ctx.eval('v()',timeout=1000,max_memory=32*1024*1024)
            if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',token):raise ValueError('行业源请求初始化失败')
            TOKEN=(time.monotonic(),token)
    return {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36','Referer':'https://q.10jqka.com.cn/','Cookie':'v='+TOKEN[1]}

def get_text(url,allow_missing=False):
    with NETWORK:
        for attempt in range(2):
            try:
                r=requests.get(url,headers=headers(),timeout=(4,10))
                if r.status_code==404 and allow_missing:return None
                r.raise_for_status()
                if not r.content:raise ValueError('行业数据源返回空响应')
                return r.text
            except (requests.ConnectionError,requests.Timeout):
                if attempt:raise
                time.sleep(1)

class CatalogParser(HTMLParser):
    def __init__(self):super().__init__();self.depth=0;self.anchor=None;self.parts=[];self.items={};self.found=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='div':
            if self.depth:self.depth+=1
            elif 'cate_inner' in attrs.get('class','').split():self.depth=1;self.found=True
        if self.depth and tag=='a':
            m=re.search(r'/thshy/detail/code/(\d{6})/?$',attrs.get('href',''))
            self.anchor=m.group(1) if m else None;self.parts=[]
    def handle_data(self,data):
        if self.anchor:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:
            code='THS'+self.anchor;name=''.join(self.parts).strip()
            if not name or code in self.items:raise ValueError('行业目录重复代码或空名称')
            self.items[code]=dict(code=code,name=name);self.anchor=None
        if tag=='div' and self.depth:self.depth-=1

def fetch_catalog():
    url='https://q.10jqka.com.cn/thshy/detail/code/881272/';raw=get_text(url);parser=CatalogParser();parser.feed(raw)
    if not parser.found or not parser.items or parser.depth:raise ValueError('行业目录结构不完整，停止替换缓存')
    items=sorted(parser.items.values(),key=lambda x:x['code'])
    return dict(items=items,total=len(items),fetched_at=core.iso(),date=core.now().astimezone(core.SH).strftime('%Y-%m-%d'),source=SOURCE,provider='ths',documentation=DOC,evidence_url=url,evidence_sha256=hashlib.sha256(raw.encode()).hexdigest())

def parse_year(text,code,year):
    native=code.removeprefix('THS')
    if not re.fullmatch(r'\d{6}',native):raise ValueError('行业代码格式不正确')
    match=re.fullmatch(r'\s*quotebridge_v4_line_bk_'+native+'_01_'+str(year)+r'\((\{.*\})\);?\s*',text,re.S)
    if not match:raise ValueError('行业历史身份或格式异常')
    payload=json.loads(match.group(1));raw=payload.get('data')
    if not isinstance(raw,str):raise ValueError('行业历史 data 字段异常')
    rows=[];seen=set()
    for record in raw.split(';'):
        if not record:continue
        cells=record.split(',')
        if len(cells)<7 or not re.fullmatch(str(year)+r'\d{4}',cells[0]):raise ValueError('历史字段或年份不符')
        d=cells[0];day=f'{d[:4]}-{d[4:6]}-{d[6:8]}';o,h,l,c,v=map(float,cells[1:6])
        if day in seen or not all(math.isfinite(x) for x in (o,h,l,c,v)) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('历史价格或重复日期校验失败')
        seen.add(day);rows.append([day,o,c,h,l,v])
    return sorted(rows,key=lambda x:x[0])

def load_year(store,code,year,end,calendar,validator):
    key=f'{PREFIX}years/{code}/{year}.json.gz';cached=store.get_json(key,optional=True)
    # A row dated today fetched intraday is not proof of a completed daily bar.
    if cached and (cached.get('closed_year') and cached['rows'] or cached.get('expected_end')==end and (cached['rows'] and cached['rows'][-1][0]>=end or cached.get('status')=='source_no_year')):return cached
    url=f'https://d.10jqka.com.cn/v4/line/bk_{code[3:]}/01/{year}.js'
    raw=get_text(url,allow_missing=True);rows=parse_year(raw,code,year) if raw is not None else []
    # Validate all returned observations inside the available calendar, including raw prices.
    within=[r for r in rows if calendar[0]<=r[0]<=calendar[-1]]
    validator(within,calendar)
    result=dict(rows=rows,expected_end=end,closed_year=year<int(end[:4]),fetched_at=core.iso(),source=SOURCE,evidence_url=url,evidence=raw,sha256=hashlib.sha256((raw or '').encode()).hexdigest(),status='ok' if rows else 'source_no_year')
    store.put_json(key,result);return result

def fetch_asset(code,name,store,start,end,calendar,validator):
    key=f'{PREFIX}prices/{end}/{code}.json.gz';last_key=f'{PREFIX}last-good/{code}.json.gz'
    cached=store.get_json(key,optional=True)
    if cached and cached.get('start')==start and cached['rows'] and cached['rows'][-1][0]==end:
        validator(cached['rows'],calendar);return cached
    try:
        full_calendar=core.calendar_dates();years=[];rows=[]
        for year in range(int(start[:4]),int(end[:4])+1):
            annual=load_year(store,code,year,end,full_calendar,validator);years.append(annual)
            rows.extend(r for r in annual['rows'] if start<=r[0]<=end)
        validator(rows,calendar)
        if not rows:raise ValueError('该行业没有所选区间的历史，暂不判断')
        value=dict(name=name,rows=rows,start=start,end=end,fetched_at=max(y['fetched_at'] for y in years),source=SOURCE,provider='ths',evidence=years)
        store.put_json(key,value);store.put_json(last_key,value);return value
    except Exception:
        old=store.get_json(last_key,optional=True)
        if not old:raise
        rows=[r for r in old['rows'] if start<=r[0]<=end]
        validator(rows,calendar)
        if not rows:raise
        return dict(old,rows=rows,start=start,end=end,refresh_error='数据源更新失败，使用上次已校验历史；请核对实际数据日期')
