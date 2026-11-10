import os
"""Validate research numbers against the artifact and exercise actual controls."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'design/v5';OUT.mkdir(exist_ok=True)
checks=[]
def check(name,passed):
    checks.append(dict(name=name,passed=bool(passed)))
    assert passed,name

with sync_playwright() as p:
    b=p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'),headless=True,args=['--no-sandbox'] if os.name!='nt' else [])
    page=b.new_page(viewport={'width':1440,'height':1100});errors=[];external=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        if r.request.url.startswith('http://127.0.0.1:8765'):r.continue_()
        else:external.append(r.request.url);r.abort()
    page.route('**/*',route)
    page.goto('http://127.0.0.1:8765/')
    page.get_by_role('link',name='共振研究 ↗').click()
    page.wait_for_function("document.querySelector('#samples').textContent !== '—'")
    r=page.request.get('http://127.0.0.1:8765/static/research/analysis.json').json()
    check('data declares back-adjustment',all(s['parameters']['fqt']=='2' for s in r['provenance']['sources']))
    check('sample count matches artifact',int(page.locator('#samples').inner_text().replace(',',''))==r['observations'])
    check('all heatmap cells and pairs',page.locator('#heatmap button').count()==256 and page.locator('#pairs tr').count()==120)
    page.screenshot(path=str(OUT/'research-desktop.png'),full_page=True)
    page.screenshot(path=str(OUT/'research-first-screen.png'))
    page.select_option('#matrix','residual')
    cell=page.locator('#heatmap button[data-a="600519.SH"][data-b="000858.SZ"]');cell.click()
    value=r['matrices']['residual']['600519.SH']['000858.SZ']
    check('matrix metric switches to correct residual',cell.inner_text()==f'{value:.2f}' and f'{value:.3f}' in page.locator('#pair-detail').inner_text())
    page.fill('#search-pair','宁德')
    check('pair filter',page.locator('#pairs tr').count()==15)
    page.select_option('#sort','holdout')
    visible=page.locator('#pairs tr').all_inner_texts()
    expected=sorted([p for p in r['pairs'] if '宁德' in p['names']],key=lambda p:p['holdout'],reverse=True)
    check('numeric sorting',expected[0]['names'] in visible[0])
    check('adjustment sensitivity disclosed','0.037' in page.locator('#sensitivity').inner_text())
    check('all downloads resolve',all(page.request.get('http://127.0.0.1:8765/static/research/'+link.get_attribute('href')).ok for link in page.locator('a[download]').all()))
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(OUT/'research-mobile.png'),full_page=True)
    check('mobile no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    check('no JavaScript errors',not errors)
    check('no external browser dependencies',not external)
    (OUT/'validation.json').write_text(json.dumps(dict(checks=checks,errors=errors),ensure_ascii=False,indent=2))
    print(json.dumps(dict(passed=len(checks),errors=errors)));b.close()
