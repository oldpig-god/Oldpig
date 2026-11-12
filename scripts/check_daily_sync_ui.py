"""Real local browser, mocked queue progression only; prices remain real archived data."""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
 page=b.new_page();page.clock.install();page.goto('http://127.0.0.1:8765/stocks?symbol=000034.SZ');page.wait_for_function("document.querySelector('#stock-symbol').textContent==='000034.SZ'")
 assert '应有交易日' in page.locator('#data-freshness').inner_text()
 # Simulate the cloud status endpoint so the polling trigger is deterministic.
 sequence=[0];reads=[]
 page.on('request',lambda r:reads.append(r.url) if '/analysis' in r.url else None)
 def status(route):
  sequence[0]+=1
  route.fulfill(json={'enabled':True,'target_date':'2026-11-12','total':1,'counts':{'current':1,'pending':0,'retry':0,'exhausted':0},'security':{'status':'current','checked_at':str(sequence[0])}})
 page.route('**/api/daily-sync/status?*',status)
 page.evaluate("const s=document.createElement('script');s.type='application/json';s.src='/cloud-ui.js';document.head.append(s)")
 page.clock.fast_forward(61000);page.wait_for_function("document.querySelector('#daily-sync-status').textContent.includes('定时采集已启用')")
 page.clock.fast_forward(61000);page.wait_for_timeout(500);assert reads,'changed durable job version must reload real analysis'
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 b.close();print({'passed':3,'checks':['freshness dates','queue update reloads real analysis','mobile no overflow']})
