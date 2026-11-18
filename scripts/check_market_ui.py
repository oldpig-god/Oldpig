"""End-to-end Playwright check of the stock-screening UI (观澜 · A股策略筛选台).

Requires the local server on BASE (default http://127.0.0.1:8765) with at least one screen run.
Never clicks 运行筛选 (network-heavy); it only checks the button exists.
Screenshots + validation.json go to design/v6/.
"""
import json
import os
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get('WORKBENCH_URL', 'http://127.0.0.1:8765').rstrip('/')
OUT = Path(__file__).resolve().parents[1] / 'design/v6'
OUT.mkdir(parents=True, exist_ok=True)
checks = []


def check(name, value, detail=None):
    checks.append({'name': name, 'passed': bool(value), **({'detail': detail} if detail is not None else {})})
    print(('PASS ' if value else 'FAIL ') + name + (f'  [{detail}]' if detail is not None else ''), flush=True)
    if not value:
        (OUT / 'validation.json').write_text(json.dumps({'checks': checks}, ensure_ascii=False, indent=2))
        raise AssertionError(name)


def browser_path():
    env = os.environ.get('CHROME_PATH')
    if env:
        return env
    for name in ('google-chrome', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found
    return None  # playwright's bundled chromium


def no_h_overflow(page):
    return page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def overflowing(page):
    """Visible elements whose content is clipped horizontally without a scroll/ellipsis affordance."""
    return page.evaluate('''() => {
      const bad = [];
      for (const el of document.querySelectorAll('.gl-topbar *, .gl-watch-top *, .gl-security *, .gl-toolbar *, .gl-checks *, .gl-tabs *')) {
        if (!el.offsetParent) continue;
        const r = el.getBoundingClientRect();
        if (r.width && r.right > innerWidth + 1) bad.push((el.id || el.className || el.tagName) + '@' + Math.round(r.right));
      }
      return bad.slice(0, 8);
    }''')


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=browser_path(), headless=True,
                              args=['--no-sandbox'] if os.name != 'nt' else [])
        ctx = b.new_context(viewport={'width': 1440, 'height': 960}, color_scheme='dark')
        page = ctx.new_page()
        errors, external, failed = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: m.type == 'error' and errors.append('console: ' + m.text))
        page.on('response', lambda r: r.status >= 400 and failed.append(f'{r.status} {r.url}'))

        def route(r):
            if r.request.url.startswith(BASE):
                r.continue_()
            else:
                external.append(r.request.url)
                r.abort()
        page.route('**/*', route)
        api = lambda path: page.request.get(BASE + path).json()

        scr = api('/api/screen?industry_band=true')
        scr_off = api('/api/screen?industry_band=false')
        check('API has a screen run', scr.get('run') is not None)
        passers = [i for i in scr['items'] if i['passed']]
        check('API passed count consistent', scr['passed'] == len(passers), scr['passed'])

        page.goto(BASE)
        page.wait_for_function("document.querySelector('#pass-count').textContent !== '—'")
        page.wait_for_function("!document.querySelector('.gl-skel')")
        page.wait_for_function("document.querySelector('#chart-loading').hidden && document.querySelector('#name').textContent !== '读取数据…'")

        # ── list shows exactly the passers
        rows = page.locator('#gl-watch-list .gl-watch-item')
        listed = rows.evaluate_all('els => els.map(e => e.dataset.symbol)')
        check('top stat count matches /api/screen', page.locator('#pass-count').inner_text() == str(scr['passed']))
        check('candidate count matches /api/screen', page.locator('#cand-count').inner_text() == str(len(scr['items'])))
        check('list renders every passer', sorted(listed) == sorted(i['symbol'] for i in passers), len(listed))
        check('list title shows count', str(scr['passed']) in page.locator('#list-title').inner_text())
        check('as-of date shown', page.locator('#asof').inner_text() == (scr['run'].get('as_of') or '—'))
        check('default sort is market cap desc',
              listed[:3] == [i['symbol'] for i in sorted(passers, key=lambda i: -(i['market_cap'] or 0))][:3])
        check('run-screen button exists', page.locator('#screen-run').is_visible())

        # ── funnel / screen table
        funnel = page.locator('#funnel .gl-step')
        check('funnel steps match API', funnel.count() == len(scr['funnel']), funnel.count())
        check('funnel final step equals passed count',
              page.locator('#funnel .gl-step.final strong').inner_text().replace(',', '') == str(scr['passed']))
        check('screen table lists passers', page.locator('#screen-rows tr[data-row]').count() == len(passers))

        # ── first stock selected; chart + check chips
        sel = page.locator('.gl-watch-item[aria-pressed="true"]')
        check('first passer auto-selected', sel.count() == 1 and sel.get_attribute('data-symbol') == listed[0])
        chips = page.locator('#screen-checks .gl-check')
        check('check chips rendered for selection', chips.count() == 8, chips.count())
        check('passer has no failing chip', page.locator('#screen-checks .gl-check.no').count() == 0)

        # select another passer via click
        if len(listed) > 1:
            target = listed[min(3, len(listed) - 1)]
            page.locator(f'.gl-watch-item[data-symbol="{target}"]').click()
            page.wait_for_function(f"document.querySelector('#symbol').textContent.startsWith({json.dumps(target)})")
            page.wait_for_function("document.querySelector('#chart-loading').hidden")
            series = api(f'/api/series/{target}?interval=1d&limit=2000')
            if series['records']:
                shown = float(page.locator('#price').inner_text().replace(',', ''))
                check('selected chart price matches stored daily close', abs(shown - series['records'][-1]['close']) < 1e-6, shown)
            check('chart canvas drawn', page.locator('#chart canvas').count() > 0)
            check('chips follow selection', page.locator('#screen-checks .gl-check').count() == 8
                  and page.locator('#screen-rows tr.is-selected').get_attribute('data-row') == target)
        page.screenshot(path=str(OUT / 'desktop-dark.png'))

        # ── band toggle
        page.click('#band')
        page.wait_for_function("document.querySelector('#band').getAttribute('aria-pressed') === 'false'")
        page.wait_for_function("document.querySelector('#list-title').textContent.includes('未用行业PE条件')")
        n_off = page.locator('#gl-watch-list .gl-watch-item').count()
        check('band off: list count matches API', n_off == scr_off['passed'], f'{n_off} vs on={scr["passed"]}')
        check('band off: stat count matches API', page.locator('#pass-count').inner_text() == str(scr_off['passed']))
        check('band off: funnel shows skipped band step', page.locator('#funnel .gl-step.skipped').count() >= 1)
        check('band off: chips mark pe_band not enabled', '未启用' in page.locator('#screen-checks').inner_text())
        page.click('#band')
        page.wait_for_function("document.querySelector('#band').getAttribute('aria-pressed') === 'true'")
        page.wait_for_function(f"document.querySelectorAll('#gl-watch-list .gl-watch-item').length === {len(passers)}")
        check('band on restores list', True)

        # ── search inside passers
        if passers:
            name = passers[0]['name']
            page.fill('#search', name)
            page.wait_for_function(f"document.querySelector('#gl-watch-list .gl-watch-item[data-symbol={json.dumps(passers[0]['symbol'])}]')")
            check('search filters passers', page.locator(f'#gl-watch-list .gl-watch-item[data-symbol="{passers[0]["symbol"]}"]').count() == 1)

        # ── search a non-passer via the universe section
        passed_set = {i['symbol'] for i in passers}
        q = '000001'
        hits = [h for h in api('/api/universe?kind=stock&limit=20&q=' + q)['items'] if h['symbol'] not in passed_set]
        if not hits:
            q = '600000'
            hits = [h for h in api('/api/universe?kind=stock&limit=20&q=' + q)['items'] if h['symbol'] not in passed_set]
        check('universe has a non-passer to search', bool(hits), q)
        other = hits[0]['symbol']
        page.fill('#search', q)
        page.wait_for_selector('#gl-watch-list .gl-section')
        item = page.locator(f'#gl-watch-list .gl-watch-item.is-fail[data-symbol="{other}"]')
        item.wait_for()
        check('non-passer appears in universe section', item.count() == 1, other)
        item.click()
        page.wait_for_function(f"document.querySelector('#symbol').textContent.startsWith({json.dumps(other)})")
        page.wait_for_function("document.querySelector('#chart-loading').hidden")
        chk = page.locator('#screen-checks').inner_text()
        in_pool = any(i['symbol'] == other for i in scr['items'])
        check('non-passer chips explain result', ('✗' in chk or '?' in chk) if in_pool else '不在候选池' in chk, chk[:60])
        page.screenshot(path=str(OUT / 'search-nonpasser-dark.png'))
        page.fill('#search', '')
        page.wait_for_function(f"document.querySelectorAll('#gl-watch-list .gl-watch-item').length === {len(passers)}")
        check('clearing search restores list', not page.locator('#gl-watch-list .gl-section').count())
        if listed:
            page.locator(f'.gl-watch-item[data-symbol="{listed[0]}"]').click()
            page.wait_for_function("document.querySelector('#chart-loading').hidden")

        # ── tabs
        page.click('#show-failed')
        page.wait_for_function(f"document.querySelectorAll('#screen-rows tr[data-row]').length === {len(scr['items'])}")
        check('show-failed lists all candidates', True)
        page.click('#show-failed')
        page.click('[data-tab="bars"]')
        check('bars tab', page.locator('#tab-bars').is_visible() and not page.locator('#tab-screen').is_visible())
        check('bars tab rows', page.locator('#bar-rows tr').count() > 0)
        page.click('[data-tab="quality"]')
        page.wait_for_function("document.querySelector('#quality-rows').children.length > 0")
        check('quality tab', page.locator('#tab-quality').is_visible())
        page.click('[data-tab="screen"]')
        check('screen tab back', page.locator('#tab-screen').is_visible())

        # ── interval switch
        page.click('[data-interval="5m"]')
        page.wait_for_function("document.querySelector('#chart-loading').hidden && document.querySelector('[data-interval=\"5m\"]').getAttribute('aria-pressed')==='true'")
        page.click('[data-interval="1d"]')
        page.wait_for_function("document.querySelector('#chart-loading').hidden")
        page.wait_for_timeout(300)

        check('desktop no horizontal overflow', no_h_overflow(page))
        check('desktop no clipped controls', not overflowing(page), overflowing(page))
        page.screenshot(path=str(OUT / 'desktop-dark.png'))

        page.click('#theme')
        page.wait_for_function("document.documentElement.dataset.theme === 'light'")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / 'desktop-light.png'))

        # ── ~800 wide
        page.set_viewport_size({'width': 800, 'height': 1000})
        page.wait_for_timeout(400)
        check('800px no horizontal overflow', no_h_overflow(page))
        check('800px no clipped controls', not overflowing(page), overflowing(page))
        page.screenshot(path=str(OUT / 'narrow-light.png'), full_page=True)
        page.click('#theme')
        page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / 'narrow-dark.png'), full_page=True)

        # ── phone
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(400)
        check('mobile no horizontal overflow', no_h_overflow(page))
        page.screenshot(path=str(OUT / 'mobile-dark.png'), full_page=True)

        check('no JavaScript errors', not errors, errors[:3])
        check('no failed requests', not failed, failed[:3])
        check('no external browser requests', not external, external[:3])
        (OUT / 'validation.json').write_text(json.dumps({'checks': checks, 'errors': errors, 'external': external, 'failed': failed},
                                                        ensure_ascii=False, indent=2))
        print(json.dumps({'passed': len(checks), 'screenshots': str(OUT)}, ensure_ascii=False))
        b.close()


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print('CHECK FAILED:', e, file=sys.stderr)
        sys.exit(1)
