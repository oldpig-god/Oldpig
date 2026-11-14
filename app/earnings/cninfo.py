"""CNINFO public announcement metadata and original files; independent of AKShare runtime."""
import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin
import requests
from .schema import DataError, SH

HEADERS={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}
REPORT = re.compile(r'(?P<year>20\d{2})年(?P<kind>年度|半年度|第一季度|第三季度|一季度|三季度)报告')

class Source:
    def __init__(self, min_interval=.25):
        self.session=requests.Session();self.session.headers.update(HEADERS)
        self.min_interval=min_interval;self.last=0;self.inventory=None

    def _request(self, method, url, **kwargs):
        time.sleep(max(0,self.min_interval-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        response=self.session.request(method,url,timeout=(5,25),**kwargs)
        response.raise_for_status()
        return response

    def companies(self):
        if self.inventory is None:
            self.inventory=self._request('GET','https://www.cninfo.com.cn/new/data/szse_stock.json').json()['stockList']
        return self.inventory

    def announcements(self, code, start, end, keyword='报告'):
        matches=[x for x in self.companies() if x['code']==code]
        if not matches:
            hits=self._request('POST','https://www.cninfo.com.cn/new/information/topSearch/detailOfQuery',data={'keyWord':code,'maxNum':10}).json()
            matches=[dict(x,zwjc=x.get('zwjc') or x.get('name')) for x in hits if x.get('code')==code and x.get('orgId')]
        if len(matches)!=1:raise DataError('巨潮公司身份未唯一匹配：'+code)
        company=matches[0];items=[];page=1
        while True:
            payload=dict(pageNum=page,pageSize=30,column='szse',tabName='fulltext',plate='',
                stock=code+','+company['orgId'],searchkey=keyword,category='',trade='',
                seDate=start+'~'+end,sortName='time',sortType='desc',isHLtitle='false')
            data=self._request('POST','https://www.cninfo.com.cn/new/hisAnnouncement/query',data=payload).json()
            items.extend(data.get('announcements') or [])
            if not data.get('hasMore'):break
            page+=1
        return company,items

    def download(self, announcement):
        path=announcement['adjunctUrl']
        if not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/[A-Za-z0-9_.-]+\.[Pp][Dd][Ff]',path):
            raise DataError('巨潮公告文件路径格式不符')
        url=urljoin('https://static.cninfo.com.cn/',path)
        response=self._request('GET',url)
        if not response.content.startswith(b'%PDF-'):raise DataError('公告文件不是 PDF')
        return url,response.content


def report_period(title):
    if '摘要' in title or '英文' in title or '取消' in title:return None
    m=REPORT.search(re.sub(r'\s+','',title))
    if not m:return None
    return m['year']+'-'+{'年度':'12-31','半年度':'06-30','第一季度':'03-31','第三季度':'09-30','一季度':'03-31','三季度':'09-30'}[m['kind']]


def metadata(company, symbol, announcement, url, captured_at):
    day=datetime.fromtimestamp(announcement['announcementTime']/1000,SH).date().isoformat()
    return dict(source_id='cninfo',entity_id='cninfo_'+company['orgId'],symbol=symbol,name=company['zwjc'],
        announcement_id=announcement['announcementId'],title=announcement['announcementTitle'],
        published_at=day,time_precision='day',ingested_at=captured_at,source_url=url,
        media_type='application/pdf',publication_metadata=announcement)
