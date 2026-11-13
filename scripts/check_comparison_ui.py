import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path(__file__).resolve().parents[1]/'design/comparison';out.mkdir(exist_ok=True)
checks=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8765/compare');page.wait_for_function("document.querySelectorAll('#left-members button').length===6");assert page.locator('#right-members button').count()==6;checks.append('editable coal and component presets')
 page.locator('#compare-run').click();page.locator('#compare-report:not([hidden])').wait_for(timeout=120000);assert '煤炭代表组合' in page.locator('#report-title').inner_text();assert page.locator('#rolling-chart svg').count()==1;assert page.locator('#scatter-chart circle').count()>500;checks.append('real adjusted basket comparison renders covariance and both charts')
 page.screenshot(path=str(out/'desktop.png'));r=page.request.get('http://127.0.0.1:8765'+page.locator('#report-download').get_attribute('href'));assert r.ok and 'snapshot' in r.json();checks.append('frozen evidence downloadable')
 for side,code in [('left','600031'),('right','510300')]:
  page.locator('#'+side+'-mode').select_option('single');page.locator('#'+side+'-query').fill(code);page.locator('#'+side+'-results button').first.wait_for();page.locator('#'+side+'-query').press('Enter');assert page.locator('#'+side+'-members button').count()==1
 page.locator('#lookback').select_option('252');page.locator('#compare-run').click();page.locator('#compare-report:not([hidden])').wait_for(timeout=120000);assert '三一重工' in page.locator('#report-title').inner_text();assert page.locator('#residual-value').inner_text()=='不可定义';checks.append('arbitrary stock versus ETF and zero benchmark residual handled')
 page.locator('#left-mode').select_option('coal');page.locator('#left-members button').first.click();assert page.locator('#left-mode').input_value()=='custom';assert page.locator('#left-members button').count()==5;checks.append('editing sector creates explicit custom basket')
 page.locator('#right-mode').select_option('custom');page.locator('#compare-run').click();assert '至少一只' in page.locator('#compare-message').inner_text();checks.append('empty side validation')
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'mobile.png'));checks.append('mobile no horizontal overflow')
 page.get_by_role('button',name='切换浅色模式',exact=True).click();assert page.evaluate('document.documentElement.dataset.theme')=='light';checks.append('light theme')
 assert not errors,errors;checks.append('no runtime errors');b.close()
(out/'browser-validation.json').write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2));print({'passed':len(checks)})
