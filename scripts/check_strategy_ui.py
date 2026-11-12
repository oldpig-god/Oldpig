"""Real browser smoke test against a running LOCAL service; uses live public data."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765');args=parser.parse_args()
out=Path(__file__).resolve().parents[1]/'design/strategy';out.mkdir(exist_ok=True)
checks=[];errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.url+'/strategy');page.wait_for_function("!document.querySelector('#history').textContent.includes('尚未加载')")
    checks.append('strategy route and experiment archive load')
    if not page.locator('#run').is_visible():page.locator('#config-toggle').click()
    page.locator('#run').click();page.wait_for_function("document.querySelector('#run-status').textContent.startsWith('已保存') || !document.querySelector('#error').hidden",timeout=120000)
    if page.locator('#error').is_visible():raise RuntimeError(page.locator('#error').inner_text())
    page.locator('#report').wait_for(state='visible');assert page.locator('#nav-chart svg').count()==1
    assert '600031.SH' in page.locator('#report-title').inner_text();checks.append('live Sany data runs and creates persisted experiment')
    page.get_by_role('tab',name='交易账本').click();assert page.locator('#trades').is_visible();page.get_by_role('tab',name='稳健性检验').click();assert page.locator('#walk-results').is_visible();page.get_by_role('tab',name='收益与风险').click()
    assert page.locator('#comparison tr').count()==17;assert page.locator('#trades tr').count()>0;checks.append('metrics, benchmark chart and execution ledger render')
    page.locator('#replay').click();page.wait_for_function("document.querySelector('#replay-status').textContent.includes('验证通过')",timeout=120000);checks.append('exact snapshot replay matches')
    page.locator('#period').select_option('full');assert page.locator('#trades tr').count()>0;checks.append('period selector updates charts and trades')
    page.locator('#period').select_option('holdout')
    page.locator('#nav-chart').scroll_into_view_if_needed();page.locator('#nav-chart').hover();assert '同股持有' in page.locator('#chart-readout').inner_text();checks.append('chart pointer shows point values')
    page.screenshot(path=str(out/'desktop.png'),full_page=True)
    eid=page.locator('#history').input_value();page.reload();page.wait_for_selector('#report:not([hidden])');assert page.locator('#history').input_value()==eid;checks.append('reload restores latest experiment')
    response=page.request.get(args.url+'/api/strategy/experiments/'+eid+'/download');record=response.json();assert 'evidence' in record['data'];assert record['result']['data_hash']==record['data']['data_hash'];checks.append('download contains frozen evidence, config and hash')
    page.set_viewport_size({'width':390,'height':844});page.reload();page.wait_for_selector('#report:not([hidden])')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'mobile.png'),full_page=True);checks.append('390px mobile has no document overflow')
    page.goto(args.url+'/');assert page.locator('a[href="/strategy"]').count()>0
    page.goto(args.url+'/technical');assert page.locator('a[href="/strategy"]').count()>0;checks.append('market and technical navigation link to strategy')
    assert not errors,errors;checks.append('no browser runtime errors')
    browser.close()
result=dict(checks=checks,errors=errors,experiment_id=eid,symbol=record['result']['symbol'],data_end=record['result']['data_end'],holdout=record['result']['holdout']['metrics'],random={k:v for k,v in record['result']['random'].items() if k not in ('returns','matched_returns')})
(out/'browser-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'passed':len(checks),'experiment':eid,'symbol':result['symbol'],'data_end':result['data_end']},ensure_ascii=False))
