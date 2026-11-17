(() => {
'use strict';
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const root = $('#gl-desk');
const PAGE = 50;

const state = {
  assets: [], selected: '510300.SH', interval: '1d', filter: 'all', sortMode: 'code', tab: 'bars',
  ma: true, vol: true, offset: 0, pageTotal: 0, historySource: 'primary', sessionDay: 'latest', focus: false, lastJob: false,
};
let chart = null, current = null;
let seriesAbort = null, seriesReq = 0, universeAbort = null, universeReq = 0, toastTimer = 0;

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
  if (result.mode === 'cloud') { await loadUniverse(); await loadSeries(); await loadCoverage(); }
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
      lines: [{color: accent, size: 1.2}, {color: ma2, size: 1.2}, {color: '#a07cff', size: 1.2}],
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
  const hasMA = chart.getIndicatorByPaneId('candle_pane', 'MA');
  const wantMA = state.ma && state.interval !== '1m';
  if (wantMA && !hasMA) chart.createIndicator({name: 'MA', calcParams: [5, 10, 20]}, true, {id: 'candle_pane'});
  if (!wantMA && hasMA) chart.removeIndicator('candle_pane', 'MA');
  const hasVOL = chart.getIndicatorByPaneId('vol_pane', 'VOL');
  if (state.vol && !hasVOL) chart.createIndicator({name: 'VOL', calcParams: [5, 10]}, false, {id: 'vol_pane', height: 86, minHeight: 50, gap: {top: 0.32, bottom: 0.02}});
  if (!state.vol && hasVOL) chart.removeIndicator('vol_pane', 'VOL');
  $('#ma').disabled = state.interval === '1m';
  $('#ma').setAttribute('aria-pressed', String(wantMA));
  $('#vol').setAttribute('aria-pressed', String(state.vol));
}

/* ───────── watchlist ───────── */
function freshnessDot(a) {
  if (!a.latest) return 'none';
  if (a.latest.close === null) return 'none';
  return /超3分钟|未知/.test(a.freshness?.label || '') ? 'stale' : 'ok';
}
function renderWatch() {
  const list = $('#gl-watch-list');
  list.innerHTML = state.assets.length ? state.assets.map(a => {
    const p = a.latest?.pct_change_source;
    const when = a.latest ? (a.latest.event_time ? '行情 ' : '抓取 ') + time(a.latest.event_time || a.latest.fetched_at).slice(5, 16) : '尚无快照';
    return `<button class="gl-watch-item" role="option" data-symbol="${esc(a.symbol)}" aria-pressed="${a.symbol === state.selected}" title="${esc(a.freshness?.label)} · ${esc(when)}">
      <span><span class="gl-w-name">${esc(a.name)}</span>
        <span class="gl-w-meta"><i class="gl-w-dot ${freshnessDot(a)}"></i>${esc(a.symbol)}<span class="gl-w-badge">${a.kind === 'etf' ? 'ETF' : a.exchange}</span></span></span>
      <span class="gl-w-right"><span class="gl-w-price">${fmt(a.latest?.close, digits(a))}</span><span class="gl-pct ${dir(p)}">${pct(p)}</span></span>
    </button>`;
  }).join('') : '<p class="gl-empty">没有匹配的证券</p>';
  $$('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === state.filter)));
  const {offset, pageTotal} = state;
  $('#page-info').textContent = `${pageTotal ? offset + 1 : 0}–${Math.min(offset + state.assets.length, pageTotal)} / ${pageTotal}`;
  $('#prev-page').disabled = offset === 0;
  $('#next-page').disabled = offset + state.assets.length >= pageTotal;
}
function markSelected() {
  $$('.gl-watch-item').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.symbol === state.selected)));
  $(`.gl-watch-item[data-symbol="${CSS.escape(state.selected)}"]`)?.scrollIntoView({block: 'nearest'});
}

async function loadUniverse({skeleton = false} = {}) {
  universeAbort?.abort(); universeAbort = new AbortController();
  const id = ++universeReq;
  if (skeleton) $('#gl-watch-list').innerHTML = '<div class="gl-skel"></div>'.repeat(10);
  const q = new URLSearchParams({q: $('#search').value.trim(), kind: state.filter, exchange: $('#exchange').value, offset: state.offset, limit: PAGE, sort: state.sortMode});
  try {
    const d = await api('/api/universe?' + q, {signal: universeAbort.signal});
    if (id !== universeReq) return;
    state.assets = d.items; state.pageTotal = d.total;
    $('#asset-count').textContent = fmt(d.universe_total, 0);
    renderWatch();
  } catch (e) { if (e.name !== 'AbortError') throw e; }
}

/* ───────── series ───────── */
function setQuoteClass(el, v) { el.classList.remove('up', 'down'); const d = dir(v); if (d) el.classList.add(d); }

function renderSeries(data) {
  current = data;
  const a = data.instrument, rows = data.records, last = rows.at(-1), d = digits(a);
  const p = last?.pct_change_source ?? null;
  $('#history-origin').hidden = !data.provenance;
  if (data.provenance) $('#history-origin').href = data.provenance.repository + '/blob/' + data.provenance.commit + '/' + data.provenance.path.split('/').map(encodeURIComponent).join('/');
  $('#adjustment').textContent = data.adjustment;
  $('#history-audit').textContent = data.audit ? `重叠 ${data.audit.compared_days} 日 · OHLC差异 ${data.audit.ohlc_mismatch_days} 日` : '';
  $('#history-source option[value=github]').disabled = !data.github_available;
  $$('[data-interval]').forEach(b => { b.disabled = state.historySource === 'github' && b.dataset.interval !== '1d'; });

  $('#name').textContent = a.name;
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
  let src = `来源：${data.source}　行情时点：${time(last?.event_time)}　抓取：${time(last?.fetched_at)}`;
  if (data.last_batch?.status === 'error') src += '　⚠ 最近抓取失败，当前展示已保存数据';
  $('#source').textContent = src; $('#source').title = src;
  $('#status').textContent = state.historySource === 'github' ? 'GitHub 固定版本历史；源未声明复权口径；不覆盖主行情'
    : state.interval === '1m' ? '分时价格序列；未将缺失 OHLC 补成 K 线' : '已校验价格 K 线；不复权，不计算投资收益率';

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
  const asset = state.assets.find(a => a.symbol === state.selected);
  if (state.historySource === 'github' && asset?.github_available === false) { state.historySource = 'primary'; $('#history-source').value = 'primary'; }
  seriesAbort?.abort(); seriesAbort = new AbortController();
  const id = ++seriesReq;
  if (asset) $('#name').textContent = asset.name;
  $('#freshness').textContent = '读取中…';
  $('#chart-loading').hidden = false;
  $$('[data-interval]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.interval === state.interval)));
  try {
    const limit = state.historySource === 'github' ? 10000 : 2000;
    const data = await api(`/api/series/${encodeURIComponent(state.selected)}?interval=${state.interval}&source=${state.historySource}&limit=${limit}`, {signal: seriesAbort.signal});
    if (id === seriesReq) renderSeries(data);
  } catch (e) {
    if (e.name !== 'AbortError') { error('价格数据读取失败：' + e.message); $('#freshness').textContent = '读取失败'; chart?.clearData(); }
  } finally {
    if (id === seriesReq) $('#chart-loading').hidden = true;
  }
}

/* ───────── coverage / quality ───────── */
const bar = (sel, n, total) => { $(sel).style.width = total ? Math.min(100, n / total * 100).toFixed(1) + '%' : '0'; };
async function loadCoverage() {
  const d = await api('/api/coverage');
  let stock = 0, etf = 0;
  d.instruments.forEach(r => { if (r.kind === 'stock') stock += r.count; else etf += r.count; });
  const hist = i => d.history.find(r => r.interval === i)?.symbols || 0;
  const tasks = d.tasks.filter(r => r.interval === '1d'), n = s => tasks.find(r => r.status === s)?.count || 0;
  const taskTotal = tasks.reduce((s, r) => s + r.count, 0);
  $('#coverage-universe').textContent = `A股 ${stock.toLocaleString()} · ETF ${etf.toLocaleString()}`;
  $('#coverage-quotes').textContent = `${d.valid_quote_prices.toLocaleString()} / ${d.total.toLocaleString()}`; bar('#bar-quotes', d.valid_quote_prices, d.total);
  $('#coverage-daily').textContent = `${hist('1d').toLocaleString()} / ${d.total.toLocaleString()} 只`; bar('#bar-daily', hist('1d'), d.total);
  $('#coverage-tasks').textContent = `已完成 ${n('done').toLocaleString()} · 待处理 ${(n('pending') + n('running')).toLocaleString()}`; bar('#bar-tasks', n('done'), taskTotal);
  $('#coverage-note').textContent = `1分钟详图已有 ${hist('1m')} 只；5分钟详图已有 ${hist('5m')} 只。日线空响应 ${n('empty')}，异常 ${n('error') + n('partial') + n('quarantined')}。记录存在不代表历史无缺口。`;
  $('#research-window').textContent = `研究窗口 ${d.history_window.start} — ${d.history_window.end}（滚动近5年）`;
  $('#coverage-source').textContent = '名录核验 ' + time(d.sources.market_inventory?.fetched_at).slice(0, 10) + ' · 快照无有效价格 ' + (d.total - d.valid_quote_prices) + ' 只';
  $('#github-coverage').textContent = d.github_history ? `GitHub 近5年补充：${d.github_history.available_current_symbols} 只有归档文件，已缓存 ${d.github_history.cached_symbols} 只；独立源截止 ${d.github_history.cutoff}，不计入主库覆盖。` : '';
  $('#coverage-exceptions').textContent = d.missing_price_examples?.length ? '快照价格缺失：' + d.missing_price_examples.map(r => r.name + ' ' + r.symbol).join('、') : '快照价格均有效';
}

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
  ['bars', 'coverage', 'quality'].forEach(t => { $('#tab-' + t).hidden = t !== value; });
  $$('[data-tab]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tab === value)));
  if (value === 'quality') loadQuality();
  if (value === 'coverage') loadCoverage().catch(e => error(e.message));
}

/* ───────── health polling ───────── */
async function health() {
  try {
    const h = await api('/api/health');
    const mode = $('#mode');
    mode.textContent = h.mode === 'cloud' ? '云端源数据' : '本地源数据'; mode.classList.remove('offline');
    $('#daily-count').textContent = big(h.counts['1d']);
    $('#minute-count').textContent = big(h.counts['1m']);
    $('#market').textContent = h.market.label;
    $('.gl-market-pill').dataset.state = h.market.state;
    $('#clock').textContent = time(h.server_time).slice(11, 19);
    $('#auto').setAttribute('aria-pressed', String(h.auto_refresh.enabled));
    $('#auto span').textContent = h.mode === 'cloud'
      ? (h.auto_refresh.enabled ? '关闭当前证券轮询' : '开启当前证券轮询')
      : (h.auto_refresh.enabled ? '关闭全市场轮询' : '开启全市场轮询');
    $('#refresh').disabled = h.job.running || state.historySource === 'github';
    $('#market-refresh').disabled = h.job.running;
    const job = $('#job-status');
    job.textContent = h.job.running ? `采集中 ${h.job.progress}/${h.job.total}` : h.job.error || (h.mode === 'cloud' ? '云端保存' : '本地保存');
    job.classList.toggle('busy', h.job.running);
    if (h.job.error && !h.job.running && state.lastJob) error(h.job.error);
    if (state.lastJob && !h.job.running) {
      await loadUniverse(); await loadSeries();
      if (state.tab === 'quality') await loadQuality();
      if (state.tab === 'coverage') await loadCoverage();
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
  state.selected = symbol; markSelected();
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
    else if (b.dataset.filter) { state.filter = b.dataset.filter; state.offset = 0; await loadUniverse({skeleton: true}); }
    else if (b.dataset.tab) switchTab(b.dataset.tab);
    else if (b.dataset.batch) await evidence(b.dataset.batch);
    else if (b.hasAttribute('data-close-evidence')) $('#batch-evidence').hidden = true;
    else switch (b.id) {
      case 'sort': state.sortMode = state.sortMode === 'name' ? 'code' : 'name'; b.textContent = state.sortMode === 'name' ? '按代码排序' : '按名称排序'; $('#watch-sort').value = state.sortMode; state.offset = 0; await loadUniverse(); break;
      case 'prev-page': case 'next-page':
        state.offset = Math.max(0, state.offset + (b.id === 'next-page' ? PAGE : -PAGE));
        await loadUniverse({skeleton: true}); $('#gl-watch-list').scrollTop = 0; break;
      case 'backfill': await action('/api/backfill', {symbols: [state.selected], intervals: ['1d']}); await loadCoverage(); error('日线补采已触发'); break;
      case 'market-refresh': await action('/api/market-refresh', {}); state.lastJob = true; await health(); break;
      case 'refresh': b.disabled = true; await action('/api/refresh', {symbols: [state.selected], intervals: [state.interval]}); state.lastJob = true; await health(); break;
      case 'auto': await action('/api/auto-refresh', {enabled: b.getAttribute('aria-pressed') !== 'true'}); await health(); break;
      case 'theme':
        if (window.Guanlan?.toggleTheme) window.Guanlan.toggleTheme();
        else setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
        break;
      case 'focus': toggleFocus(); break;
      case 'ma': state.ma = !state.ma; indicators(); break;
      case 'vol': state.vol = !state.vol; indicators(); break;
      case 'reset': chart?.setBarSpace(state.interval === '1m' ? 3 : 8); chart?.scrollToRealTime(); chart?.resize(); break;
    }
  } catch (err) { error(err.message); await health(); }
});

$('#history-source').addEventListener('change', async () => {
  state.historySource = $('#history-source').value;
  if (state.historySource === 'github') state.interval = '1d';
  await loadSeries(); await health();
});
let searchTimer;
$('#search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.offset = 0; loadUniverse().catch(e => error(e.message)); }, 180);
});
$('#search').addEventListener('keydown', e => {
  if (e.key === 'Enter' && state.assets[0]) { e.preventDefault(); select(state.assets[0].symbol); }
  if (e.key === 'Escape') { e.target.value = ''; e.target.dispatchEvent(new Event('input')); e.target.blur(); }
});
$('#exchange').addEventListener('change', () => { state.offset = 0; loadUniverse({skeleton: true}).catch(e => error(e.message)); });
$('#watch-sort').addEventListener('change', () => {
  state.sortMode = $('#watch-sort').value;
  $('#sort').textContent = state.sortMode === 'name' ? '按代码排序' : '按名称排序';
  state.offset = 0;
  loadUniverse({skeleton: true}).catch(e => error(e.message));
});
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
    await Promise.all([loadUniverse({skeleton: true}), health()]);
    await loadSeries();
    loadCoverage().catch(e => error(e.message));
    setInterval(health, 5000);
    setInterval(() => { if (state.tab === 'coverage') loadCoverage().catch(e => error(e.message)); }, 15000);
    setInterval(async () => {
      try {
        if ($('#mode').textContent === '云端源数据' && $('#auto').getAttribute('aria-pressed') === 'true' && $('#market').textContent === '交易时段' && state.historySource === 'primary') {
          await action('/api/refresh', {symbols: [state.selected], intervals: [state.interval]});
        }
        await loadUniverse();
      } catch (e) { error(e.message); }
    }, 60000);
    matchMedia('(prefers-color-scheme: light)').addEventListener('change', ev => {
      try { if (localStorage.getItem('guanlan-theme') || localStorage.getItem('gl-theme')) return; } catch (e) { /* ignore */ }
      setTheme(ev.matches ? 'light' : 'dark', false);
    });
  } catch (e) { error('初始化失败：' + e.message); }
})();
})();
