import os
"""End-to-end whole-market paging, coverage and real price checks."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parents[1]/'design/v4';OUT.mkdir(exist_ok=True)
checks=[]
def check(name,value):
    checks.append({'name':name,'passed':bool(value)})
    assert value,name
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'),headless=True,args=['--no-sandbox'] if os.name!='nt' else [])
    page=b.new_page(viewport={'width':1440,'height':1050});errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        if r.request.url.startswith('http://127.0.0.1:8765'):r.continue_()
        else:external.append(r.request.url);r.abort()
    page.route('**/*',route)
    page.goto('http://127.0.0.1:8765');page.wait_for_function("document.querySelector('#coverage-daily').textContent!=='—'")
    cov=page.request.get('http://127.0.0.1:8765/api/coverage').json()
    check('whole-market total matches API',int(page.locator('#asset-count').inner_text().replace(',',''))==cov['total'] and cov['total']>7000)
    check('browser renders bounded first page',page.locator('[data-symbol]').count()==50)
    first=page.locator('[data-symbol]').first.get_attribute('data-symbol');page.click('#next-page');page.wait_for_function("document.querySelector('#page-info').textContent.startsWith('51')")
    check('next page changes securities',page.locator('[data-symbol]').first.get_attribute('data-symbol')!=first)
    page.click('#prev-page');page.wait_for_function("document.querySelector('#page-info').textContent.startsWith('1–')")
    check('previous page restores securities',page.locator('[data-symbol]').first.get_attribute('data-symbol')==first)
    page.select_option('#exchange','BJ');page.wait_for_function("Array.from(document.querySelectorAll('[data-symbol]')).every(x=>x.dataset.symbol.endsWith('.BJ'))")
    check('Beijing exchange filter',page.locator('[data-symbol]').count()==50)
    page.fill('#search','920002');page.wait_for_selector('[data-symbol="920002.BJ"]');page.wait_for_function("document.querySelectorAll('[data-symbol]').length===1")
    check('search finds BSE new code',page.locator('[data-symbol]').first.get_attribute('data-symbol')=='920002.BJ')
    page.locator('[data-symbol="920002.BJ"]').click();page.wait_for_function("document.querySelector('#symbol').textContent.includes('920002')")
    check('selected BSE quote distinguished from candles','最新批量快照' in page.locator('#snapshot-summary').inner_text())
    page.fill('#search','');page.select_option('#exchange','all');page.click('[data-filter="etf"]');page.wait_for_function("document.querySelector('#page-info').textContent.includes('/ 1666')")
    check('ETF filter shows all source ETFs','1666' in page.locator('#page-info').inner_text())
    page.fill('#search','510300');page.wait_for_selector('[data-symbol="510300.SH"]');page.locator('[data-symbol="510300.SH"]').click()
    page.wait_for_function("document.querySelector('#symbol').textContent.includes('510300')")
    for interval in ['1d','5m','1m']:
        page.click('[data-interval="'+interval+'"]');page.wait_for_function("document.querySelector('#chart').getAttribute('aria-label').includes(' '+"+json.dumps(interval)+"+' ')")
        api=page.request.get('http://127.0.0.1:8765/api/series/510300.SH?interval='+interval).json()
        check(interval+' displayed price matches stored data',float(page.locator('#price').inner_text().replace(',',''))==api['records'][-1]['close'])
    check('one-minute OHLC not invented',page.locator('#open').inner_text()=='—' and page.locator('#ma').is_disabled())
    page.select_option('#history-source','github');page.wait_for_function("document.querySelector('#freshness').textContent==='GitHub 历史归档'")
    check('GitHub history constrained to rolling five years',cov['history_window']['start'] in page.locator('#range').inner_text())
    check('GitHub provenance and audit visible',page.locator('#history-origin').is_visible() and '重叠' in page.locator('#history-audit').inner_text())
    check('GitHub adjustment uncertainty visible','未声明复权口径' in page.locator('#adjustment').inner_text())
    check('GitHub archive does not masquerade as minute bars',page.locator('[data-interval="1m"]').is_disabled())
    page.screenshot(path=str(OUT/'github-history.png'),full_page=True)
    page.select_option('#history-source','primary');page.wait_for_function("document.querySelector('#freshness').textContent!=='GitHub 历史归档'")
    page.click('[data-tab="quality"]');page.wait_for_selector('[data-batch]')
    check('quality scoped to selected instrument',all('510300.SH' in x for x in page.locator('#quality-rows tr').all_inner_texts()))
    page.locator('[data-batch]').first.click();page.wait_for_selector('#batch-evidence:not([hidden])');check('raw batch evidence','SHA-256' in page.locator('#batch-evidence').inner_text())
    page.click('[data-tab="bars"]');page.click('[data-interval="1d"]');page.wait_for_timeout(300)
    page.screenshot(path=str(OUT/'desktop.png'),full_page=True);page.click('#theme');page.wait_for_timeout(300);page.screenshot(path=str(OUT/'dark.png'),full_page=True);page.click('#theme')
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
    check('mobile page does not overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    check('no JavaScript errors',not errors);check('no external browser dependencies',not external)
    (OUT/'validation.json').write_text(json.dumps({'checks':checks,'errors':errors,'external':external},ensure_ascii=False,indent=2))
    print(json.dumps({'passed':len(checks),'errors':errors}));b.close()
