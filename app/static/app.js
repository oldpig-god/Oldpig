(() => {
'use strict';
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const root = $('#gl-desk');
const state = {
  assets: [], screen: null, selected: null, interval: '1d', sortMode: 'cap', tab: 'screen', band: true, showFailed: false,
  ma: true, ema: true, vol: true, macd: true, sessionDay: 'latest', focus: false, lastJob: false, searchHits: [],
};
let chart = null, current = null;
let seriesAbort = null, seriesReq = 0, searchAbort = null, searchReq = 0, toastTimer = 0;

/* ───────── formatting ───────── */
const fmt = (v, d = 3) => v === null || v === undefined || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const big = v => {
  if (v === null || v === undefined) return '—';
  const n = Number(v), a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (n / 1e4).toFixed(2) + '万';
  return fmt(n, 0);
};
const yi = v => v === null || v === undefined ? '—' : Math.round(Number(v) / 1e8).toLocaleString() + '亿';
const pct = v => v === null || v === undefined ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%';
const dir = v => v > 0 ? 'up' : v < 0 ? 'down' : '';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const tf = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
const time = v => v ? tf.format(new Date(v)) : '—';
const digits = a => a?.kind === 'etf' ? 3 : 2;
const issueNames = {open_absent_in_trend_payload: '分时开盘字段缺失或为0（价格仍保留）', unclosed_or_future_period: '周期尚未闭合或时间在未来', daily_not_final_yet: '当日日线尚未最终发布', invalid_ohlc: 'OHLC字段无效', inconsistent_ohlc: 'OHLC高低关系错误', invalid_close: '价格无效', invalid_time: '时间无效', outside_session: '非交易时段', outside_research_window: '早于近5年研究窗口', conflicting_timestamp: '同响应时间戳冲突'};

/* ───────── infra ───────── */
async function api(url, options = {}) {
  const r = await fetch(url, {cache: 'no-store', ...options});
  if (r.status === 401) { location.href = '/login'; throw Error('请重新登录'); }
  if (!r.ok) { const body = await r.json().catch(() => ({})); throw Error(body.detail || `请求失败 ${r.status}`); }
  return r.json();
}
async function action(path, body) {
  const result = await api(path, {method: 'POST', headers: {'content-type': 'application/json', 'x-workbench-action': 'local-refresh'}, body: JSON.stringify(body)});
  if (result.mode === 'cloud') {
    await loadScreen();
    await loadSeries();
    if (state.tab === 'quality') await loadQuality();
  }
  return result;
}

function error(message) {
  const box = $('#notice');
  if (!box.hidden && box.dataset.msg === message) return;
  box.dataset.msg = message;
  box.innerHTML = `${esc(message)}<button aria-label="关闭">✕</button>`;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 7000);
}
$('#notice').addEventListener('click', e => { if (e.target.closest('button')) $('#notice').hidden = true; });

/* ───────── theme & chart styles ───────── */
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const alpha = (hex, a) => { const h = hex.replace('#', ''); const n = parseInt(h.length === 3 ? h.replace(/./g, '$&$&') : h, 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };

function applyTheme() {
  if (!chart) return;
  const text = cssVar('--muted'), line = cssVar('--line'), up = cssVar('--up'), down = cssVar('--down'),
    accent = cssVar('--accent'), panel = cssVar('--panel-2'), strong = cssVar('--text-2'), ma2 = cssVar('--ma2');
  const font = {color: text, family: 'inherit', size: 11};
  chart.setStyles({
    grid: {horizontal: {color: line, style: 'dashed', dashedValue: [2, 3]}, vertical: {show: false}},
    candle: {
      bar: {upColor: up, downColor: down, noChangeColor: text, upBorderColor: up, downBorderColor: down, noChangeBorderColor: text, upWickColor: up, downWickColor: down, noChangeWickColor: text},
      area: {lineColor: accent, lineSize: 1.6, backgroundColor: [{offset: 0, color: alpha(accent, .02)}, {offset: 1, color: alpha(accent, .22)}], point: {show: false}},
      priceMark: {high: {color: strong, textSize: 10}, low: {color: strong, textSize: 10}, last: {upColor: up, downColor: down, noChangeColor: text, line: {style: 'dashed', dashedValue: [3, 3]}}},
      tooltip: {showRule: 'follow_cross', showType: 'standard', text: {...font, color: strong},
        custom: ({current: r}) => [
          {title: '时间 ', value: time(r.timestamp).slice(0, state.interval === '1d' ? 10 : 16)},
          ...(state.interval === '1m' ? [] : [{title: '开 ', value: fmt(r.open, digits(current?.instrument))}, {title: '高 ', value: fmt(r.high, digits(current?.instrument))}, {title: '低 ', value: fmt(r.low, digits(current?.instrument))}]),
          {title: '收 ', value: fmt(r.close, digits(current?.instrument))},
          {title: '量 ', value: big(r.volume)},
        ]},
    },
    indicator: {
      bars: [{upColor: alpha(up, .55), downColor: alpha(down, .55), noChangeColor: alpha(text, .5)}],
      lines: [{color: accent, size: 1.2}, {color: ma2, size: 1.2}, {color: '#a07cff', size: 1.2}, {color: '#2dd4bf', size: 1.2}],
      tooltip: {text: font},
    },
    xAxis: {axisLine: {color: line}, tickLine: {show: false}, tickText: font},
    yAxis: {axisLine: {show: false}, tickLine: {show: false}, tickText: font},
    separator: {color: line, size: 1},
    crosshair: {
      horizontal: {line: {color: text, style: 'dashed', dashedValue: [3, 3]}, text: {color: '#fff', backgroundColor: accent, borderColor: accent}},
      vertical: {line: {color: text, style: 'dashed', dashedValue: [3, 3]}, text: {color: '#fff', backgroundColor: accent, borderColor: accent}},
    },
  });
}
function setTheme(t, persist = true) {
  document.documentElement.dataset.theme = t;
  if (persist) {
    try { localStorage.setItem('gl-theme', t); localStorage.setItem('guanlan-theme', t); } catch (e) { /* private mode */ }
  }
  applyTheme();
  narrowChart = null; fitLastPriceLabel();
}
window.addEventListener('guanlan-theme', e => {
  try { localStorage.setItem('gl-theme', e.detail); } catch (err) { /* ignore */ }
  setTheme(e.detail, false);
});
// On narrow charts the y-axis has few ticks and the last-price tag covers one of them;
// keep the dashed last-price line there and rely on the large header price instead.
let narrowChart = null;
function fitLastPriceLabel() {
  if (!chart) return;
  const narrow = $('#chart').clientWidth < 760;
  if (narrow === narrowChart) return;
  narrowChart = narrow;
  chart.setStyles({candle: {priceMark: {last: {text: {show: !narrow}}}}});
}

function indicators() {
  if (!chart) return;
  const non1m = state.interval !== '1m';
  const hasMA = chart.getIndicatorByPaneId('candle_pane', 'MA');
  const wantMA = state.ma && non1m;
  if (wantMA && !hasMA) chart.createIndicator({name: 'MA', calcParams: [5, 10, 20]}, true, {id: 'candle_pane'});
  if (!wantMA && hasMA) chart.removeIndicator('candle_pane', 'MA');
  const hasEMA = chart.getIndicatorByPaneId('candle_pane', 'EMA');
  const wantEMA = state.ema && non1m;
  if (wantEMA && !hasEMA) {
    chart.createIndicator({
      name: 'EMA', calcParams: [12, 20, 50],
      styles: {lines: [
        {color: '#f59e0b', size: 1.2, style: 'dashed', dashedValue: [4, 3]},
        {color: '#ec4899', size: 1.2, style: 'dashed', dashedValue: [4, 3]},
        {color: '#14b8a6', size: 1.2, style: 'dashed', dashedValue: [4, 3]},
      ]},
    }, true, {id: 'candle_pane'});
  }
  if (!wantEMA && hasEMA) chart.removeIndicator('candle_pane', 'EMA');
  const hasVOL = chart.getIndicatorByPaneId('vol_pane', 'VOL');
  if (state.vol && !hasVOL) chart.createIndicator({name: 'VOL', calcParams: [5, 10]}, false, {id: 'vol_pane', height: 68, minHeight: 44, gap: {top: 0.30, bottom: 0.02}});
  if (!state.vol && hasVOL) chart.removeIndicator('vol_pane', 'VOL');
  const hasMACD = chart.getIndicatorByPaneId('macd_pane', 'MACD');
  const wantMACD = state.macd && non1m;
  if (wantMACD && !hasMACD) chart.createIndicator({name: 'MACD', calcParams: [12, 26, 9]}, false, {id: 'macd_pane', height: 78, minHeight: 48, gap: {top: 0.22, bottom: 0.08}});
  if (!wantMACD && hasMACD) chart.removeIndicator('macd_pane', 'MACD');
  $('#ma').disabled = !non1m;
  $('#ema').disabled = !non1m;
  $('#macd').disabled = !non1m;
  $('#ma').setAttribute('aria-pressed', String(wantMA));
  $('#ema').setAttribute('aria-pressed', String(wantEMA));
  $('#vol').setAttribute('aria-pressed', String(state.vol));
  $('#macd').setAttribute('aria-pressed', String(wantMACD));
}

/* ───────── screen list ───────── */
const RULES = [['not_st', '非ST'], ['cap', '市值>500亿'], ['pe_positive', 'PE>0'], ['pe_band', '行业PE±2σ'], ['ma', 'MA'], ['ema', 'EMA'], ['macd', 'MACD']];
const mark = v => v === true ? '<span class="gl-mark ok">✓</span>' : v === false ? '<span class="gl-mark no">✗</span>' : '<span class="gl-mark na" title="无法判断">?</span>';
const SORTS = {
  cap: (a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0),
  pct_desc: (a, b) => (b.pct_change ?? -1e9) - (a.pct_change ?? -1e9),
  pct_asc: (a, b) => (a.pct_change ?? 1e9) - (b.pct_change ?? 1e9),
  pe: (a, b) => (a.pe_ttm ?? 1e9) - (b.pe_ttm ?? 1e9),
  industry: (a, b) => String(a.industry).localeCompare(String(b.industry), 'zh') || (b.market_cap ?? 0) - (a.market_cap ?? 0),
  name: (a, b) => a.name.localeCompare(b.name, 'zh'),
};
const screenItem = symbol => state.screen?.items.find(i => i.symbol === symbol);

function visibleAssets() {
  const q = $('#search').value.trim().toUpperCase();
  const items = (state.screen?.items || []).filter(i => i.passed && (!q || i.symbol.includes(q) || i.name.toUpperCase().includes(q) || String(i.industry).toUpperCase().includes(q)));
  return items.sort(SORTS[state.sortMode] || SORTS.cap);
}

function watchRow(a, fromScreen) {
  const p = fromScreen ? a.pct_change : a.latest?.pct_change_source, price = fromScreen ? a.close : a.latest?.close;
  const sub = fromScreen ? `${yi(a.market_cap)} · PE ${fmt(a.pe_ttm, 1)}`
    : (a.screen ? (a.screen.passed ? '入选' : a.screen.fetch_error ? '候选 · 日线获取失败，未判定' : '候选未通过') : '不在候选池');
  return `<button class="gl-watch-item${!fromScreen && !a.screen?.passed ? ' is-fail' : ''}" role="option" data-symbol="${esc(a.symbol)}" aria-pressed="${a.symbol === state.selected}">
      <span><span class="gl-w-name">${esc(a.name)}</span>
        <span class="gl-w-meta">${esc(a.symbol)}<span class="gl-w-badge gl-w-ind">${esc(fromScreen ? a.industry || '—' : a.exchange)}</span></span>
        <span class="gl-w-sub">${esc(sub)}</span></span>
      <span class="gl-w-right"><span class="gl-w-price">${fmt(price, 2)}</span><span class="gl-pct ${dir(p)}">${pct(p)}</span></span>
    </button>`;
}

function renderWatch() {
  const list = $('#gl-watch-list');
  state.assets = visibleAssets();
  let html = state.assets.map(a => watchRow(a, true)).join('');
  const q = $('#search').value.trim();
  const extra = state.searchHits.filter(h => !state.assets.some(a => a.symbol === h.symbol));
  if (q && extra.length) {
    html += `<div class="gl-section">全市场搜索（不在入选结果中）</div>` + extra.map(h => watchRow({...h, screen: screenItem(h.symbol)}, false)).join('');
  }
  if (!html) html = state.screen?.run ? '<p class="gl-empty">没有匹配的入选股票</p>' : '<p class="gl-empty">尚无筛选结果，点击右上角“运行筛选”</p>';
  list.innerHTML = html;
  $('#list-title').textContent = `策略入选 ${state.screen?.passed ?? 0} 只` + (state.band ? '' : '（未用行业PE条件）');
  $('#page-info').textContent = q ? `匹配 ${state.assets.length}` : '';
  const run = state.screen?.run;
  $('#run-info').textContent = run ? `筛选于 ${time(run.created_at).slice(5, 16)}` : '';
  $('#band').setAttribute('aria-pressed', String(state.band));
}
function markSelected() {
  $$('.gl-watch-item').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.symbol === state.selected)));
  $$('#screen-rows tr').forEach(r => r.classList.toggle('is-selected', r.dataset.row === state.selected));
  $(`.gl-watch-item[data-symbol="${CSS.escape(state.selected)}"]`)?.scrollIntoView({block: 'nearest'});
}

async function loadScreen() {
  state.screen = await api('/api/screen?industry_band=' + state.band);
  const run = state.screen.run;
  $('#pass-count').textContent = run ? state.screen.passed : '—';
  $('#cand-count').textContent = run ? state.screen.items.length : '—';
  $('#asof').textContent = run?.as_of || '—';
  renderWatch(); renderScreenTable(); renderChecks();
  if (!state.selected || !screenItem(state.selected) && !current) {
    state.selected = state.assets[0]?.symbol || '600519.SH';
  }
}

async function searchUniverse() {
  const q = $('#search').value.trim();
  searchAbort?.abort();
  if (q.length < 2) { state.searchHits = []; renderWatch(); return; }
  searchAbort = new AbortController();
  const id = ++searchReq;
  try {
    const d = await api('/api/universe?' + new URLSearchParams({q, kind: 'stock', limit: 20}), {signal: searchAbort.signal});
    if (id !== searchReq) return;
    state.searchHits = d.items;
  } catch (e) { if (e.name !== 'AbortError') throw e; }
  renderWatch();
}

function bandText(i) {
  const b = i.band;
  if (!b) return '—';
  if (b.low === null) return `样本${b.n}只，不足`;
  return `${fmt(b.low, 1)} ~ ${fmt(b.high, 1)}`;
}
function renderFunnel() {
  const f = state.screen?.funnel || [];
  $('#funnel').innerHTML = f.length ? f.map((s, n) => `${n ? '<span class="gl-step-arrow">›</span>' : ''}<div class="gl-step${s.skipped ? ' skipped' : ''}${n === f.length - 1 ? ' final' : ''}"><span>${esc(s.label)}</span><strong>${s.count === null ? '跳过' : s.count.toLocaleString()}</strong></div>`).join('')
    : '<span class="gl-muted-s">尚无筛选记录</span>';
  const run = state.screen?.run, warn = $('#run-warn');
  const failed = run?.fetch_errors || 0, notes = (run?.warnings || []).map(w => String(w).split('\n')[0].slice(0, 120));
  warn.hidden = !run || (!failed && !notes.length);
  if (!warn.hidden) {
    warn.textContent = (failed ? `⚠ ${failed} 只候选的前复权日线获取失败，技术条件未判定、不计入入选；可稍后重新运行筛选。` : '⚠ ')
      + (notes.length ? `上游提示 ${notes.length} 条（悬停查看）` : '');
    warn.title = notes.join('\n');
  }
}
function renderScreenTable() {
  renderFunnel();
  const items = (state.screen?.items || []).filter(i => state.showFailed || i.passed).sort(SORTS.cap);
  $('#screen-rows').innerHTML = items.length ? items.map(i => {
    const t = i.technicals || {}, c = i.checks;
    return `<tr data-row="${esc(i.symbol)}" class="${i.symbol === state.selected ? 'is-selected' : ''}">
      <td>${esc(i.name)}<small>${esc(i.symbol)}</small></td>
      <td>${esc(i.industry || '—')}</td>
      <td>${mark(c.cap)} ${yi(i.market_cap)}</td>
      <td>${mark(c.pe_positive)} ${fmt(i.pe_ttm, 2)}</td>
      <td>${mark(c.pe_band)} ${esc(bandText(i))}${i.band && i.band.mean !== null ? `<small>均值 ${fmt(i.band.mean, 1)} · σ ${fmt(i.band.std, 1)} · n=${i.band.n}</small>` : ''}</td>
      <td>${mark(c.ma)} ${fmt(t.ma5, 2)} / ${fmt(t.ma10, 2)} / ${fmt(t.ma20, 2)}</td>
      <td>${mark(c.ema)} ${fmt(t.ema12, 2)} / ${fmt(t.ema20, 2)} / ${fmt(t.ema50, 2)}</td>
      <td>${mark(c.macd)} ${fmt(t.dif, 3)} / ${fmt(t.dea, 3)}</td>
      <td>${esc(t.bar_date || (i.fetch_error ? '获取失败' : '—'))}${i.stale ? '<small>停牌/未更新</small>' : ''}</td>
      <td class="${i.passed ? 'ok' : 'no'}">${i.passed ? '入选' : '未通过'}</td></tr>`;
  }).join('') : `<tr><td colspan="10" class="gl-empty">${state.screen?.run ? '没有通过全部条件的股票' : '尚无筛选结果，点击“运行筛选”'}</td></tr>`;
}
function renderChecks() {
  const box = $('#screen-checks');
  const i = screenItem(state.selected);
  if (!state.screen?.run) { box.innerHTML = ''; return; }
  if (!i) { box.innerHTML = '<span class="gl-check na">不在候选池：ST、总市值≤500亿或缺快照字段</span>'; return; }
  const t = i.technicals || {};
  const detail = {
    cap: yi(i.market_cap), pe_positive: fmt(i.pe_ttm, 2), pe_band: bandText(i),
    ma: t.ma5 !== undefined ? `MA5 ${fmt(t.ma5, 2)}` : '', ema: t.ema12 !== undefined ? `EMA12 ${fmt(t.ema12, 2)}` : '',
    macd: t.dif !== undefined ? `DIF ${fmt(t.dif, 3)} / DEA ${fmt(t.dea, 3)}` : '', not_st: '',
  };
  const head = i.fetch_error && !i.technicals
    ? `<span class="gl-check head" title="${esc(i.fetch_error)}">✗ 未入选 · 前复权日线获取失败，技术条件未判定</span>`
    : `<span class="gl-check head">${i.passed ? '✓ 入选' : '✗ 未通过'} · 日线 ${esc(t.bar_date || '—')}（前复权）${i.stale ? ' · 停牌/未更新' : ''}</span>`;
  box.innerHTML = head + RULES.map(([k, label]) => {
    const v = i.checks[k], skip = k === 'pe_band' && !state.band;
    return `<span class="gl-check ${skip ? '' : v === true ? 'ok' : v === false ? 'no' : 'na'}"><b>${v === true ? '✓' : v === false ? '✗' : '?'}</b>${esc(label)}${detail[k] ? ' ' + esc(detail[k]) : ''}${skip ? '（未启用）' : ''}</span>`;
  }).join('');
}

/* ───────── series ───────── */
function setQuoteClass(el, v) { el.classList.remove('up', 'down'); const d = dir(v); if (d) el.classList.add(d); }

function renderSeries(data) {
  current = data;
  const a = data.instrument, rows = data.records, last = rows.at(-1), d = digits(a);
  const p = last?.pct_change_source ?? null;
  $('#adjustment').textContent = data.adjustment + ' · 图表';

  $('#name').textContent = a.name;
  const deep = $('#stock-deep-link');
  if (deep) deep.href = '/stocks?symbol=' + encodeURIComponent(a.symbol);
  $('#symbol').textContent = a.symbol + ' · ' + (a.kind === 'etf' ? 'ETF' : 'A股') + ' · ' + ({SH: '上交所', SZ: '深交所', BJ: '北交所'}[a.exchange] || a.exchange);
  $('#kind').textContent = a.kind === 'etf' ? 'ETF' : a.exchange;
  $('#price').textContent = fmt(last?.close, d); setQuoteClass($('#price'), p);
  $('#change').textContent = pct(p); setQuoteClass($('#change'), p); $('#change').hidden = p === null;
  $('#change').title = '涨跌幅取自上游源字段，非本地计算';
  const fr = $('#freshness'); fr.textContent = data.freshness.label; fr.classList.toggle('stale', data.freshness.state === 'stale');
  ['open', 'high', 'low'].forEach(k => { $('#' + k).textContent = fmt(last?.[k], d); });
  const minute = state.interval === '1m';
  $('.gl-facts').classList.toggle('is-1m', minute);
  $$('.gl-fact-1m').forEach(el => { el.hidden = !minute; });
  $('#tick-time').textContent = last ? time(last.event_time).slice(5, 16) : '—';
  $('#volume').textContent = big(last?.volume_raw);
  $('#amount').textContent = big(last?.amount_raw);

  let viewRows = rows;
  if (state.interval !== '1d' && rows.length > 0) {
    const days = [...new Set(rows.map(r => time(r.event_time).slice(0, 10)))];
    $('#session-wrap').hidden = false;
    if (state.sessionDay !== 'all' && state.sessionDay !== 'latest' && !days.includes(state.sessionDay)) state.sessionDay = 'latest';
    $('#session-day').innerHTML =
      `<option value="latest">最新单日 (${esc(days.at(-1))})</option>` +
      `<option value="all">多日连续 (${days.length}日)</option>` +
      [...days].reverse().map(day => `<option value="${esc(day)}">${esc(day)}</option>`).join('');
    $('#session-day').value = state.sessionDay;
    const targetDay = state.sessionDay === 'latest' ? days.at(-1) : state.sessionDay === 'all' ? null : state.sessionDay;
    viewRows = targetDay ? rows.filter(r => time(r.event_time).slice(0, 10) === targetDay) : rows;
  } else {
    $('#session-wrap').hidden = true;
  }

  $('#snapshot-summary').textContent = data.quote
    ? `最新批量快照 ${fmt(data.quote.close, d)} CNY · ${data.quote.event_time ? '行情 ' + time(data.quote.event_time).slice(5, 16) : '源时点未知'} · 抓取 ${time(data.quote.fetched_at).slice(5, 16)}`
    : '暂无批量行情快照';
  $('#range').textContent = data.range.start ? `${data.range.start.slice(0, 10)} → ${data.range.end.slice(0, 10)} · ${data.range.count.toLocaleString()} 条` : '暂无已保存记录';
  const ind = data.indicators?.latest;
  $('#ind-summary').textContent = state.interval !== '1m' && ind
    ? `图表(不复权) MA5 ${fmt(ind.ma5, d)} · EMA12 ${fmt(ind.ema12, d)} · DIF ${fmt(ind.macd, 3)} · DEA ${fmt(ind.macd_signal, 3)} · MACD ${fmt(ind.macd_hist, 3)}`
    : '';
  let src = `来源：${data.source}　行情时点：${time(last?.event_time)}　抓取：${time(last?.fetched_at)}`;
  if (data.last_batch?.status === 'error') src += '　⚠ 最近抓取失败，当前展示已保存数据';
  $('#source').textContent = src; $('#source').title = src;
  $('#status').textContent = state.interval === '1m' ? '分时价格序列；未将缺失 OHLC 补成 K 线' : '已校验价格 K 线；不复权，不计算投资收益率';

  const body = $('#bar-rows');
  body.innerHTML = viewRows.length ? viewRows.slice(-200).reverse().map(r => `<tr>
      <td>${time(r.event_time).slice(0, state.interval === '1d' ? 10 : 16)}</td><td>${fmt(r.open, d)}</td>
      <td>${fmt(r.high, d)}</td><td>${fmt(r.low, d)}</td><td class="${dir(r.pct_change_source)}">${fmt(r.close, d)}</td>
      <td class="${dir(r.pct_change_source)}">${pct(r.pct_change_source)}</td><td>${big(r.volume_raw)}</td><td>${big(r.amount_raw)}</td></tr>`).join('')
    : '<tr><td colspan="8" class="gl-empty">该周期尚无数据；可点击“更新图表”。日线也可通过全市场队列补采</td></tr>';

  if (chart) {
    chart.setPriceVolumePrecision(d, 0);
    chart.setStyles({candle: {type: state.interval === '1m' ? 'area' : 'candle_solid'}});
    // Bar space first, so the y-axis range is computed for the final visible window.
    chart.setBarSpace(state.interval === '1m' ? 3 : 8);
    chart.setOffsetRightDistance(40);
    chart.applyNewData(viewRows.map(r => state.interval === '1m'
      ? {timestamp: r.timestamp, open: r.close, high: r.close, low: r.close, close: r.close, volume: r.volume_raw ?? 0}
      : {timestamp: r.timestamp, open: r.open, high: r.high, low: r.low, close: r.close, volume: r.volume_raw ?? 0}), false);
    chart.scrollToRealTime();
    indicators();
    chart.resize(); // forces a full axis recalculation
  }
  $('#chart').setAttribute('aria-label', `${a.name} ${state.interval} ${data.chart_type} 源数据价格图，共${viewRows.length}条`);
}

async function loadSeries() {
  const asset = screenItem(state.selected);
  seriesAbort?.abort(); seriesAbort = new AbortController();
  const id = ++seriesReq;
  if (asset) $('#name').textContent = asset.name;
  $('#freshness').textContent = '读取中…';
  $('#chart-loading').hidden = false;
  $$('[data-interval]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.interval === state.interval)));
  try {
    const data = await api(`/api/series/${encodeURIComponent(state.selected)}?interval=${state.interval}&limit=2000`, {signal: seriesAbort.signal});
    if (id === seriesReq) renderSeries(data);
  } catch (e) {
    if (e.name !== 'AbortError') { error('价格数据读取失败：' + e.message); $('#freshness').textContent = '读取失败'; chart?.clearData(); }
  } finally {
    if (id === seriesReq) $('#chart-loading').hidden = true;
  }
}

/* ───────── quality ───────── */
async function loadQuality() {
  try {
    const data = await api('/api/quality?symbol=' + encodeURIComponent(state.selected));
    $('#quality-rows').innerHTML = data.latest.length ? data.latest.map(r => `<tr>
      <td>${esc(r.symbol)}<small>${esc(r.interval)} / ${esc(r.endpoint)}</small></td>
      <td><span class="gl-status ${esc(r.status)}">${esc(r.status)}</span><small>${time(r.finished_at)}</small></td>
      <td>${r.rows_in}</td><td>${r.rows_accepted}</td><td>${r.rows_rejected}</td>
      <td><button data-batch="${esc(r.id)}">查看批次</button></td></tr>`).join('')
      : '<tr><td colspan="6" class="gl-empty">该证券暂无采集批次</td></tr>';
  } catch (e) { error(e.message); }
}
async function evidence(id) {
  try {
    const d = await api('/api/batches/' + encodeURIComponent(id));
    const box = $('#batch-evidence'); box.hidden = false;
    const issues = d.parsed_issues ?? (typeof d.issues === 'string' ? JSON.parse(d.issues || '{}') : (d.issues || {}));
    box.innerHTML = `<div class="gl-evidence-head"><h3>批次证据</h3><button class="gl-btn gl-ghost" data-close-evidence>收起</button></div><p>${esc(d.symbol)} / ${esc(d.interval)} / ${esc(d.endpoint)}</p><p>原始文件：${esc(d.raw_path)}${d.raw_missing ? '（本地未找到该原始文件）' : ''}<br>SHA-256：${esc(d.raw_sha256)}</p><pre></pre>`;
    box.querySelector('pre').textContent = JSON.stringify({说明: issueNames, 检查计数: issues, 异常样本: d.issue_examples, 源数据样本: d.raw_sample, ...(d.cloud_note ? {云端归档说明: d.cloud_note} : {})}, null, 2);
    box.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (e) { error(e.message); }
}

function switchTab(value) {
  state.tab = value;
  ['screen', 'bars', 'quality'].forEach(t => { $('#tab-' + t).hidden = t !== value; });
  $$('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tab === value)));
  if (value === 'quality') loadQuality();
}

/* ───────── health polling ───────── */
async function health() {
  try {
    const h = await api('/api/health');
    const mode = $('#mode');
    mode.textContent = h.mode === 'cloud' ? '云端源数据' : '本地源数据'; mode.classList.remove('offline');
    $('#market').textContent = h.market.label;
    $('.gl-market-pill').dataset.state = h.market.state;
    $('#clock').textContent = time(h.server_time).slice(11, 19);
    $('#auto').setAttribute('aria-pressed', String(h.auto_refresh.enabled));
    $('#auto span').textContent = h.mode === 'cloud'
      ? (h.auto_refresh.enabled ? '关闭当前证券轮询' : '开启当前证券轮询')
      : (h.auto_refresh.enabled ? '关闭自动' : '开启自动');
    $('#refresh').disabled = h.job.running;
    $('#screen-run').disabled = h.job.running;
    const job = $('#job-status'), stage = {snapshot: '抓取全市场快照', technicals: '候选前复权日线', chart_bars: '更新入选图表日线'}[h.job.stage];
    job.textContent = h.job.running ? (h.job.kind === 'screen' ? `筛选中 · ${stage || ''} ${h.job.total ? h.job.progress + '/' + h.job.total : ''}` : `采集中 ${h.job.progress}/${h.job.total}`) : h.job.error || (h.mode === 'cloud' ? '云端保存' : '本地保存');
    job.classList.toggle('busy', h.job.running);
    if (h.job.error && !h.job.running && state.lastJob) error(h.job.error);
    if (state.lastJob && !h.job.running) {
      await loadScreen(); await loadSeries();
      if (state.tab === 'quality') await loadQuality();
    }
    state.lastJob = h.job.running;
  } catch (e) {
    $('#mode').textContent = '服务离线'; $('#mode').classList.add('offline');
    error('本地服务暂不可用：' + e.message);
  }
}

/* ───────── interactions ───────── */
async function select(symbol) {
  if (!symbol || symbol === state.selected) return;
  state.selected = symbol; markSelected(); renderChecks();
  await loadSeries();
  if (state.tab === 'quality') loadQuality();
}
function toggleFocus() {
  state.focus = !state.focus;
  root.dataset.focus = String(state.focus);
  $('#focus').setAttribute('aria-label', state.focus ? '恢复侧栏' : '展开图表');
  requestAnimationFrame(() => chart?.resize());
}

root.addEventListener('click', async e => {
  const b = e.target.closest('button');
  if (!b || b.disabled) return;
  try {
    if (b.dataset.symbol) await select(b.dataset.symbol);
    else if (b.dataset.interval) { state.interval = b.dataset.interval; await loadSeries(); }
    else if (b.dataset.tab) switchTab(b.dataset.tab);
    else if (b.dataset.batch) await evidence(b.dataset.batch);
    else if (b.hasAttribute('data-close-evidence')) $('#batch-evidence').hidden = true;
    else switch (b.id) {
      case 'band': state.band = !state.band; await loadScreen(); break;
      case 'screen-run': b.disabled = true; { const res = await action('/api/screen/run', {refresh: true}); state.lastJob = Boolean(res?.running); } await health(); break;
      case 'refresh': b.disabled = true; { const res = await action('/api/refresh', {symbols: [state.selected], intervals: [state.interval]}); state.lastJob = Boolean(res?.running); } await health(); break;
      case 'auto': await action('/api/auto-refresh', {enabled: b.getAttribute('aria-pressed') !== 'true'}); await health(); break;
      case 'theme': setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); break;
      case 'focus': toggleFocus(); break;
      case 'ma': state.ma = !state.ma; indicators(); break;
      case 'ema': state.ema = !state.ema; indicators(); break;
      case 'vol': state.vol = !state.vol; indicators(); break;
      case 'macd': state.macd = !state.macd; indicators(); break;
      case 'reset': chart?.setBarSpace(state.interval === '1m' ? 3 : 8); chart?.scrollToRealTime(); chart?.resize(); break;
    }
  } catch (err) { error(err.message); await health(); }
});

let searchTimer;
$('#search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  renderWatch();
  searchTimer = setTimeout(() => { searchUniverse().catch(e => error(e.message)); }, 220);
});
$('#search').addEventListener('keydown', e => {
  const first = $('#gl-watch-list [data-symbol]')?.dataset.symbol;
  if (e.key === 'Enter' && first) { e.preventDefault(); select(first); }
  if (e.key === 'Escape') { e.target.value = ''; e.target.dispatchEvent(new Event('input')); e.target.blur(); }
});
$('#watch-sort').addEventListener('change', () => { state.sortMode = $('#watch-sort').value; renderWatch(); markSelected(); });
$('#show-failed').addEventListener('change', () => { state.showFailed = $('#show-failed').checked; renderScreenTable(); });
$('#screen-rows').addEventListener('click', e => { const r = e.target.closest('tr[data-row]'); if (r) select(r.dataset.row).catch(err => error(err.message)); });
$('#session-day').addEventListener('change', () => {
  state.sessionDay = $('#session-day').value;
  if (current) renderSeries(current);
});

document.addEventListener('keydown', e => {
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
  if (e.key === '/' && !typing) { e.preventDefault(); $('#search').focus(); $('#search').select(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const i = state.assets.findIndex(a => a.symbol === state.selected);
    const next = state.assets[Math.max(0, Math.min(state.assets.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
    if (next) { e.preventDefault(); select(next.symbol); }
  } else if (e.key === 'f' || e.key === 'F') toggleFocus();
});

/* ───────── boot ───────── */
(async () => {
  try {
    if (window.klinecharts) {
      chart = klinecharts.init($('#chart'), {locale: 'zh-CN', timezone: 'Asia/Shanghai'});
      // Keep head-room for the MA legend / high mark and foot-room for the low mark.
      chart.setPaneOptions({id: 'candle_pane', gap: {top: 0.16, bottom: 0.12}});
      applyTheme();
      new ResizeObserver(() => { chart?.resize(); fitLastPriceLabel(); }).observe($('#chart'));
    } else {
      $('#chart').innerHTML = '<p class="gl-empty">图表组件加载失败，仍可查看行情明细</p>';
    }
    $('#gl-watch-list').innerHTML = '<div class="gl-skel"></div>'.repeat(10);
    await Promise.all([loadScreen(), health()]);
    markSelected(); renderChecks();
    await loadSeries();
    setInterval(health, 5000);
    setInterval(async () => {
      if (state.lastJob) return;
      try {
        if ($('#mode').textContent === '云端源数据' && $('#auto').getAttribute('aria-pressed') === 'true' && $('#market').textContent === '交易时段') {
          await action('/api/refresh', {symbols: [state.selected], intervals: [state.interval]});
        }
        await loadScreen();
      } catch (e) { error(e.message); }
    }, 60000);
    matchMedia('(prefers-color-scheme: light)').addEventListener('change', ev => {
      try { if (localStorage.getItem('guanlan-theme') || localStorage.getItem('gl-theme')) return; } catch (e) { /* ignore */ }
      setTheme(ev.matches ? 'light' : 'dark', false);
    });
  } catch (e) { error('初始化失败：' + e.message); }
})();
})();
