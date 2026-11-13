import json,time,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8766';root=Path(__file__).resolve().parents[1]/'design/data-health';checks=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));tick=time.monotonic()
 page.goto(base+'/data-health');page.locator('#metrics .metric').first.wait_for(timeout=90000);page.wait_for_function("document.querySelector('#details').textContent!=='正在读取明细…'",timeout=90000);load=time.monotonic()-tick
 checks.append('overview and first page rendered');page.screenshot(path=str(root/'desktop.png'))
 page.locator('#metrics [data-view="gaps"]').click();page.locator('#details table').wait_for();checks.append('gap drilldown rendered')
 page.locator('#views [data-view="all"]').click();page.locator('#query').fill('600031');page.locator('#filters button[type="submit"]').click();page.wait_for_function("document.querySelector('#result-count').textContent==='1 只'");checks.append('backend symbol search')
 page.locator('#details [data-repair]').last.click();page.locator('#confirm-repair[open]').wait_for();page.locator('#confirm-repair button[value="cancel"]').click();checks.append('range repair dialog without unintended submit')
 for view in ['tasks','sources','calendar','storage','config']:
  page.locator(f'#views [data-view="{view}"]').click();page.wait_for_function("document.querySelector('#details').textContent!=='正在读取明细…'");checks.append(view+' panel')
 page.set_viewport_size({'width':390,'height':844});page.locator('#reload').click();page.locator('#metrics .metric').first.wait_for();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(root/'mobile.png'));checks.append('mobile no horizontal page overflow');assert not errors,errors;b.close()
report=dict(checks=checks,first_render_seconds=round(load,3),scope='Full local audited universe; first navigation in a new browser context; warm API process',errors=errors);(root/'local-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
