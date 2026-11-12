"""Real-browser acceptance for all-market research and the shared visual system."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path(__file__).resolve().parents[1]/'design/terminal-v3';out.mkdir(exist_ok=True)
checks=[];errors=[];bad_assets=[]
u='http://127.0.0.1:8765'
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1440,'height':1050})
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('response',lambda r:bad_assets.append(r.url) if r.status>=400 and '/static/' in r.url else None)
 page.goto(u+'/stocks');page.wait_for_function("document.querySelector('#stock-name').textContent==='三一重工'")
 assert page.locator('#stock-chart canvas').count()>0;checks.append('real Sany daily candles, indicators and date')
 page.screenshot(path=str(out/'stocks-desktop.png'))
 page.locator('#global-security-query').fill('688001');page.locator('#global-search-results a').first.wait_for();page.locator('#global-security-query').press('ArrowDown');page.locator('#global-security-query').press('Enter');page.wait_for_function("document.querySelector('#stock-symbol').textContent==='688001.SH'");checks.append('global keyboard search selects STAR equity')
 page.locator('#security-board').select_option('beijing');page.wait_for_function("document.querySelector('#security-list').textContent.includes('北交所')");page.locator('#security-list button').first.click();page.wait_for_function("document.querySelector('#stock-board').textContent==='北交所'");checks.append('Beijing board history and indicators')
 page.goto(u+'/stocks?symbol=600079.SH');page.wait_for_function("document.querySelector('#stock-risk').hidden===false");assert page.locator('#stock-strategy').get_attribute('aria-disabled')=='true';assert page.locator('#stock-rsi').inner_text()!='—';checks.append('ST observations available, unsupported backtest disabled')
 page.goto(u+'/stocks?symbol=159915.SZ');page.wait_for_function("document.querySelector('#stock-board').textContent==='ETF'");assert page.locator('#stock-rsi').inner_text()!='—';checks.append('ETF observations use real history')
 page.locator('[data-range="60"]').click();assert page.locator('[data-range="60"]').get_attribute('aria-pressed')=='true';checks.append('chart history range controls')
 page.get_by_role('button',name='切换浅色模式',exact=True).click();assert page.evaluate('document.documentElement.dataset.theme')=='light';page.screenshot(path=str(out/'stocks-light.png'));page.reload();assert page.evaluate('document.documentElement.dataset.theme')=='light';checks.append('light mode persists across reload')
 page.get_by_role('button',name='切换深色模式',exact=True).click()
 page.set_viewport_size({'width':390,'height':844});page.reload();page.wait_for_function("document.querySelector('#stock-board').textContent==='ETF'");assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'stocks-mobile.png'));checks.append('390px stock page no horizontal overflow')
 page.get_by_role('button',name='展开导航').click();page.locator('.terminal-sidebar a[href="/strategy"]').click();page.wait_for_selector('#report:not([hidden])');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');checks.append('mobile drawer navigates to strategy')
 page.get_by_role('tab',name='交易账本').click();assert page.locator('#trades').is_visible();page.get_by_role('tab',name='稳健性检验').click();assert page.locator('#random-result').is_visible();page.get_by_role('tab',name='方法与证据').click();assert page.locator('#assumptions').is_visible();page.get_by_role('tab',name='收益与风险').click();checks.append('all strategy tabs reveal their complete content')
 page.screenshot(path=str(out/'strategy-mobile.png'));page.set_viewport_size({'width':1440,'height':1050});page.screenshot(path=str(out/'strategy-desktop.png'))
 page.goto(u+'/strategy?symbol=688001.SH');page.wait_for_function("document.querySelector('#symbol').value==='688001.SH'");assert page.locator('#run').is_visible();assert not page.locator('#report').is_visible();checks.append('security links prefill a new experiment without displaying another security result')
 page.goto(u+'/technical');page.wait_for_function("document.querySelector('#selected-name').textContent.length>1");assert not page.locator('.selection-panel').is_visible();assert page.locator('#price-chart svg').is_visible();page.locator('#technical-quick-switch').select_option(index=1);assert '东山精密' in page.locator('#selected-name').inner_text();page.locator('#technical-selection-toggle').click();assert page.locator('.selection-panel').is_visible();page.locator('#technical-selection-toggle').click();page.screenshot(path=str(out/'technical-desktop.png'));checks.append('technical chart first, stock switcher and collapsible editor')
 for route in ['/', '/technical','/static/research/index.html']:
  page.goto(u+route);page.wait_for_timeout(600);assert page.locator('.terminal-sidebar').count()==1;page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route;page.set_viewport_size({'width':1440,'height':1050})
 checks.append('market, technical and correlation pages share shell and fit mobile')
 assert not errors,errors;assert not bad_assets,bad_assets;checks.append('no JavaScript errors or missing static assets')
 b.close()
(out/'browser-validation.json').write_text(json.dumps({'checks':checks,'errors':errors,'missing_assets':bad_assets},ensure_ascii=False,indent=2));print({'passed':len(checks)})
