"""Exercise six-stock research controls with saved real data; no upstream calls."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'design/v6';OUT.mkdir(exist_ok=True)
checks=[]
def check(name,passed):
    checks.append(dict(name=name,passed=bool(passed)))
    assert passed,name


with sync_playwright() as p:
    b=p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'),headless=True,args=['--no-sandbox'] if os.name!='nt' else [])
    page=b.new_page(viewport={'width':1440,'height':1100});errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(request):
        if request.request.url.startswith('http://127.0.0.1:8765'):request.continue_()
        else:external.append(request.request.url);request.abort()
    page.route('**/*',route)
    page.goto('http://127.0.0.1:8765/');page.get_by_role('link',name='技术面研究 ↗').click()
    page.wait_for_selector('#stock-rows tr');r=page.request.get('http://127.0.0.1:8765/api/technical').json()
    check('six specified stocks',page.locator('#stock-rows tr').count()==6 and all(s in page.locator('#stock-rows').inner_text() for s in r['watchlist']))
    check('daily cutoff and quote time separate',r['expected_end'] in page.locator('#daily-asof').inner_text() and '2026-11-11' in page.locator('#quote-asof').inner_text())
    current=r['assets'][0];q=next(q for q in r['quotes'] if q['symbol']==current['symbol'])
    check('quote not inserted into candle data',len(r['series'][current['symbol']])==r['dates']['calendar_sessions'] and all(row['date'] in r['provenance']['calendar'] for row in r['series'][current['symbol']]))
    check('displayed close matches input',float(page.locator('#daily-price').inner_text())==current['latest']['raw_close'])
    check('three historical rules and low sample warnings',page.locator('#event-stats tr').count()==3 and page.locator('#event-stats .badge.low').count()==3)
    page.locator('[data-select="600031.SH"]').click()
    sany=next(a for a in r['assets'] if a['symbol']=='600031.SH')
    check('stock selection updates chart and metrics','三一重工' in page.locator('#selected-name').inner_text() and float(page.locator('#daily-price').inner_text())==sany['latest']['raw_close'])
    page.locator('#price-chart svg').focus();page.keyboard.press('ArrowLeft')
    check('keyboard chart inspection',r['series']['600031.SH'][-2]['date'] in page.locator('#chart-readout').inner_text())
    page.select_option('#range','60')
    check('range changes plotted dates',r['series']['600031.SH'][-60]['date'] in page.locator('#price-chart').inner_text())
    before=page.locator('#price-chart path').count();page.uncheck('#bands')
    check('Bollinger overlay toggle',page.locator('#price-chart path').count()==before-2)
    page.select_option('#period','train')
    check('historical period toggle',r['dates']['train_start'] in page.locator('#study-dates').inner_text() and page.locator('#event-stats tr').count()==3)
    page.select_option('#correlation','residual')
    a,bkey='002463.SZ','002938.SZ';value=r['comovement']['residual'][a][bkey]
    cell=page.locator(f'#heatmap button[data-a="{a}"][data-b="{bkey}"]');cell.click()
    check('residual correlation matches data',cell.inner_text()==f'{value:.2f}' and f'{value:.3f}' in page.locator('#co-selected').inner_text())
    check('all downloads available',all(page.request.get('http://127.0.0.1:8765'+x.get_attribute('href')).ok for x in page.locator('a[download]').all()))
    # A simulated upstream failure must preserve visible results and re-enable retry.
    page.route('**/api/technical/refresh',lambda request:request.fulfill(status=502,content_type='application/json',body=json.dumps({'detail':'模拟上游不可用'})))
    page.click('#refresh');page.wait_for_selector('#error:not([hidden])')
    check('refresh failure preserves results',page.locator('#stock-rows tr').count()==6 and page.locator('#refresh').is_enabled() and '模拟上游不可用' in page.locator('#error').inner_text())
    page.unroute('**/api/technical/refresh')
    page.reload();page.wait_for_selector('#stock-rows tr')
    page.screenshot(path=str(OUT/'technical-desktop.png'),full_page=True);page.screenshot(path=str(OUT/'technical-first-screen.png'))
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'technical-mobile.png'),full_page=True)
    check('mobile no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    check('no browser exceptions',not errors);check('no external browser dependencies',not external)
    (OUT/'validation.json').write_text(json.dumps(dict(checks=checks,errors=errors,external=external),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=len(checks),errors=errors)));b.close()
