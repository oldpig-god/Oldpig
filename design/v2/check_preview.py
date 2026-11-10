"""Interaction checks for the temporary standalone renderer, not production tests."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1056,'height':1450})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('file:///tmp/finance-workbench-v2-preview.html')
    f = page.frames[1]
    f.wait_for_selector('#gl-candles canvas')
    page.wait_for_timeout(300)
    assert f.evaluate('klinecharts.version()') == '9.8.12'
    assert f.locator('.gl-watch-item').count() == 6
    f.locator('#gl-desk').screenshot(path=str(OUT/'desktop-light.png'))
    page.emulate_media(color_scheme='dark')
    page.wait_for_timeout(100)
    f.locator('#gl-desk').screenshot(path=str(OUT/'desktop-dark.png'))
    page.emulate_media(color_scheme='light')
    f.locator('#gl-search').fill('510300')
    assert f.locator('.gl-watch-item').count() == 1
    f.locator('#gl-search').fill('不存在')
    assert f.locator('.gl-empty').is_visible()
    f.locator('#gl-search').fill('')
    f.locator('[data-filter="stock"]').click()
    assert f.locator('.gl-watch-item').count() == 2
    f.locator('[data-code="600519"]').click()
    assert f.locator('#gl-name').inner_text() == '贵州茅台'
    assert f.locator('#gl-price').inner_text() == '1486.20'
    f.locator('[data-detail="bars"]').click()
    assert f.locator('#gl-bar-rows tr').first.locator('td').nth(4).inner_text() == '1486.20'
    f.locator('[data-filter="all"]').click()
    f.locator('#gl-sort').click()
    assert f.locator('.gl-watch-item').first.get_attribute('data-code') == '159915'
    f.locator('[data-period="weekly"]').click()
    assert '周线' in f.locator('#gl-bars-title').inner_text()
    f.locator('[data-period="minute"]').click()
    rows = f.locator('#gl-bar-rows tr').all_text_contents()
    assert '15:00' in rows[0] and '14:40' in rows[-1]
    old_count = f.locator('#gl-candles canvas').count()
    f.locator('#gl-ma').click()
    assert f.locator('#gl-ma').get_attribute('aria-pressed') == 'false'
    f.locator('#gl-macd').click()
    assert f.locator('#gl-candles canvas').count() > old_count
    f.locator('#gl-macd').click()
    f.locator('#gl-ma').click()
    f.locator('#gl-focus').click()
    assert not f.locator('.gl-watch-panel').is_visible()
    f.locator('#gl-reset').click()
    f.locator('#gl-focus').click()
    f.locator('[data-detail="etf"]').click()
    f.locator('#gl-etf-sort').select_option('fee')
    assert 'ETF B' in f.locator('#gl-etf-rows tr').first.inner_text()
    f.locator('[data-workspace="quality"]').click()
    assert f.locator('#gl-detail-quality').is_visible()
    assert '964' in f.locator('#gl-detail-quality').inner_text()
    f.locator('#gl-desk').screenshot(path=str(OUT/'quality.png'))
    f.locator('[data-code="510300"]').click()
    f.locator('[data-period="daily"]').click()
    f.locator('[data-detail="etf"]').click()
    f.locator('#gl-theme').click()
    assert f.locator('#gl-desk').evaluate('(el)=>el.style.colorScheme') == 'dark'
    f.locator('#gl-theme').click()
    sizes = []
    for width in [768,390,320]:
        page.set_viewport_size({'width':width,'height':1900})
        page.wait_for_timeout(150)
        dim = f.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
        assert dim['scroll'] <= dim['width'], dim
        sizes.append({'viewport':width,**dim})
        if width==390: f.locator('#gl-desk').screenshot(path=str(OUT/'mobile.png'))
    assert not errors, errors
    # Simulate an unavailable chart dependency without relying on browser cache interception.
    wrapper = Path('/tmp/finance-workbench-v2-preview.html').read_text()
    tag = '&lt;script src=&quot;https://cdn.jsdelivr.net/npm/klinecharts@9.8.12/dist/umd/klinecharts.min.js&quot;&gt;&lt;/script&gt;'
    assert tag in wrapper
    missing_library = Path('/tmp/finance-workbench-v2-missing-library.html')
    missing_library.write_text(wrapper.replace(tag, ''))
    failure = browser.new_page(viewport={'width':1056,'height':1450})
    failure.goto(missing_library.as_uri())
    ff = failure.frames[1]
    ff.wait_for_selector('#gl-candles [role="alert"]')
    ff.locator('[data-detail="bars"]').click()
    assert ff.locator('#gl-bar-rows tr').count()==5
    report={'library':'KLineChart 9.8.12','errors':errors,'responsive':sizes,'checks':['real chart library loaded','search','empty search','asset class filter','linked price and source table','watchlist sort','weekly aggregation view','minute session timestamps','MA toggle','MACD pane created and removed','focus mode','zoom reset','ETF fee sort','quality snapshot','theme switch','responsive overflow','missing chart dependency fallback']}
    (OUT/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    browser.close()
