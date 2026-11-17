'use strict';
const $ = id => document.getElementById(id);
const f = n => Number(n).toFixed(3);
const pct = n => (100 * n).toFixed(1) + '%';
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

let result = null;
let selectedPair = null;

function isDark() {
  return document.documentElement.dataset.theme === 'dark';
}

function color(v) {
  const dark = isDark();
  const target = dark
    ? (v >= 0 ? [214, 69, 84] : [58, 122, 214])
    : (v >= 0 ? [178, 71, 83] : [40, 94, 145]);
  const base = dark ? [22, 29, 43] : [242, 246, 249];
  const a = Math.min(1, Math.abs(v));
  return `rgb(${base.map((x, i) => Math.round(x + (target[i] - x) * a)).join(',')})`;
}

function cellText(v) {
  if (isDark()) return Math.abs(v) > 0.55 ? '#ffffff' : '#e7ebf3';
  return Math.abs(v) > 0.65 ? '#fff' : '#27405a';
}

function isSelected(r, c) {
  if (!selectedPair) return false;
  const [a, b] = selectedPair;
  return (r === a && c === b) || (r === b && c === a);
}

function syncSelection() {
  for (const id of ['heatmap', 'covariance-heatmap']) {
    const table = $(id);
    if (!table) continue;
    for (const btn of table.querySelectorAll('button[data-a][data-b]')) {
      const active = isSelected(btn.dataset.a, btn.dataset.b);
      btn.classList.toggle('is-selected', active);
      btn.setAttribute('aria-pressed', String(active));
    }
  }
}

function syncThemeButton() {
  const btn = $('research-theme');
  if (!btn) return;
  const dark = isDark();
  btn.textContent = dark ? '浅色模式' : '深色模式';
  btn.setAttribute('aria-pressed', String(!dark));
  btn.setAttribute('aria-label', '明暗切换');
  btn.title = dark ? '明暗切换（当前：深色）' : '明暗切换（当前：浅色）';
}

function setTheme(t, persist = true) {
  const theme = t === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try { localStorage.setItem('gl-theme', theme); localStorage.setItem('guanlan-theme', theme); } catch (e) { /* private mode */ }
  }
  syncThemeButton();
  if (result) {
    matrix();
    covariance();
  }
}

function detail(a, b) {
  selectedPair = [a, b];
  syncSelection();
  const box = $('pair-detail');
  if (a === b) {
    const name = result.assets[a] || a;
    const variance = result.matrices.sample_cov_daily && result.matrices.sample_cov_daily[a]
      ? result.matrices.sample_cov_daily[a][a]
      : null;
    const varText = Number.isFinite(variance) ? variance.toExponential(3) : '—';
    box.textContent = `${name}：自身相关性为 1；日收益率方差为 ${varText}。`;
    return;
  }
  const p = result.pairs.find(x => (x.a === a && x.b === b) || (x.a === b && x.b === a));
  if (!p) {
    box.textContent = '未找到该股票对明细。';
    return;
  }
  box.innerHTML =
    `<strong>${esc(p.names)}</strong> ` +
    `近5年相关性 ${esc(f(p.correlation))}　·　扣除大盘后 ${esc(f(p.residual))}　·　留出期 ${esc(f(p.holdout))}` +
    `<br>日收益率协方差 ${esc(p.covariance_daily.toExponential(4))}　·　Spearman ${esc(f(p.spearman))}　·　连续60交易日滚动范围 ${esc(f(p.rolling_min))} ～ ${esc(f(p.rolling_max))}`;
}

function matrix() {
  const key = $('matrix').value;
  const m = result.matrices[key];
  const order = result.order;
  $('heatmap').innerHTML =
    `<thead><tr><th scope="col">Pearson r</th>${order.map(s => `<th scope="col">${esc(result.assets[s])}</th>`).join('')}</tr></thead>` +
    `<tbody>${order.map(a =>
      `<tr><th scope="row">${esc(result.assets[a])}</th>${order.map(b => {
        const v = m[a][b];
        const sel = isSelected(a, b);
        return `<td><button${sel ? ' class="is-selected"' : ''} aria-pressed="${sel}" data-a="${esc(a)}" data-b="${esc(b)}" style="background:${color(v)};color:${cellText(v)}" aria-label="${esc(result.assets[a])}与${esc(result.assets[b])}：${esc(f(v))}">${esc(v.toFixed(2))}</button></td>`;
      }).join('')}</tr>`
    ).join('')}</tbody>`;
}

function covariance() {
  const key = $('covariance').value;
  const m = result.matrices[key];
  const order = result.order;
  const max = Math.max(...order.flatMap(a => order.map(b => Math.abs(m[a][b])))) || 1;
  $('covariance-unit').textContent =
    key === 'price_cov_yuan2'
      ? '后复权理论价格协方差，单位：理论元²。受价格水平和趋势影响。'
      : key === 'sample_cov_daily'
        ? '日对数收益率协方差；对角线为各股票的日收益率方差。'
        : '年化对数收益率协方差（日协方差 × 252）；对角线为年化方差。';
  $('covariance-heatmap').innerHTML =
    `<thead><tr><th scope="col">协方差</th>${order.map(a => `<th scope="col">${esc(result.assets[a])}</th>`).join('')}</tr></thead>` +
    `<tbody>${order.map(a =>
      `<tr><th scope="row">${esc(result.assets[a])}</th>${order.map(b => {
        const v = m[a][b];
        const t = v / max;
        const sel = isSelected(a, b);
        return `<td><button${sel ? ' class="is-selected"' : ''} aria-pressed="${sel}" data-a="${esc(a)}" data-b="${esc(b)}" style="background:${color(t)};color:${cellText(t)}" aria-label="${esc(result.assets[a])}与${esc(result.assets[b])}协方差：${esc(v.toExponential(3))}">${esc(v.toExponential(2))}</button></td>`;
      }).join('')}</tr>`
    ).join('')}</tbody>`;
}

function pairs() {
  const q = $('search-pair').value.trim().toLowerCase();
  const key = $('sort').value;
  const total = result.pairs.length;
  const ps = result.pairs
    .filter(p => !q || (p.names + p.a + p.b).toLowerCase().includes(q))
    .sort((a, b) => b[key] - a[key]);
  $('pair-count').textContent = `${ps.length} / ${total} 组`;
  $('pairs').innerHTML = ps.map(p =>
    `<tr><td>${esc(p.names)}<small>${esc(p.a)} / ${esc(p.b)}</small></td>` +
    `<td>${esc(f(p.correlation))}</td>` +
    `<td>${esc(p.covariance_daily.toExponential(3))}</td>` +
    `${['train', 'holdout', 'residual', 'recent60'].map(k => `<td class="${p[k] >= 0.7 ? 'strong' : ''}">${esc(f(p[k]))}</td>`).join('')}` +
    `<td>${p.holdout_ci95 ? esc(p.holdout_ci95.map(f).join(' — ')) : '未估计'}</td></tr>`
  ).join('');
}

function bindEvents() {
  const themeBtn = $('research-theme');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      if (window.Guanlan?.toggleTheme) window.Guanlan.toggleTheme();
      else setTheme(isDark() ? 'light' : 'dark', true);
    });
  }
  window.addEventListener('guanlan-theme', e => {
    try { localStorage.setItem('gl-theme', e.detail); } catch (err) { /* ignore */ }
    setTheme(e.detail, false);
  });
  const lightMedia = matchMedia('(prefers-color-scheme: light)');
  lightMedia.addEventListener('change', ev => {
    try { if (localStorage.getItem('guanlan-theme') || localStorage.getItem('gl-theme')) return; } catch (e) { /* ignore */ }
    setTheme(ev.matches ? 'light' : 'dark', false);
  });
  window.addEventListener('storage', e => {
    if ((e.key === 'gl-theme' || e.key === 'guanlan-theme') && (e.newValue === 'dark' || e.newValue === 'light')) {
      setTheme(e.newValue, false);
    }
  });
  $('matrix').addEventListener('change', () => { if (result) matrix(); });
  $('covariance').addEventListener('change', () => { if (result) covariance(); });
  $('covariance-heatmap').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (b && result) {
      detail(b.dataset.a, b.dataset.b);
      $('pair-detail').scrollIntoView({block: 'center', behavior: 'smooth'});
    }
  });
  $('heatmap').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (b && result) detail(b.dataset.a, b.dataset.b);
  });
  $('sort').addEventListener('change', () => { if (result) pairs(); });
  $('search-pair').addEventListener('input', () => { if (result) pairs(); });
}

async function init() {
  syncThemeButton();
  bindEvents();
  try {
    const response = await fetch('analysis.json', {cache: 'no-store'});
    if (!response.ok) throw Error('研究数据尚未生成');
    result = await response.json();
    $('date').textContent = `${result.window_start} — ${result.source_end}`;
    $('generated').textContent = '生成于 ' + new Date(result.generated_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai'}) + '（上海）';
    $('samples').textContent = result.observations.toLocaleString();
    $('pca').textContent = pct(result.pca_variance_ratio[0]);
    $('shrinkage').textContent = pct(result.shrinkage);
    $('split').textContent = `训练期 ${result.train_start} — ${result.train_end}（${result.train_rows}日）；留出期 ${result.holdout_start} — ${result.holdout_end}（${result.holdout_rows}日）。默认只按训练期排序，所有120组均可查看。`;

    const missing = result.quality.filter(x => x.missing_sessions);
    $('quality').textContent = `窗口内${result.provenance.calendar.length}个交易日；${missing.map(x => result.assets[x.symbol] + '缺' + x.missing_sessions + '日').join('，')}。共同收益率样本剔除${result.excluded_return_sessions}个缺失相关交易日，另首日无前值。缺失原因未独立核验。`;

    const get = (a, b) => result.pairs.find(p => (p.a === a && p.b === b) || (p.a === b && p.b === a));
    const liquor = get('600519.SH', '000858.SZ') || result.pairs[0];
    const bank = get('600036.SH', '601166.SH');
    const ev = get('300750.SZ', '002594.SZ');
    const cards = [];
    if (liquor) {
      cards.push(`<article><b>白酒：共同波动仍然明显</b><p>茅台 / 五粮液全样本相关性 ${esc(f(liquor.correlation))}，扣除大盘后 ${esc(f(liquor.residual))}，留出期 ${esc(f(liquor.holdout))}。近期60个共同样本日为 ${esc(f(liquor.recent60))}，强度有所变化。</p></article>`);
    }
    if (bank) {
      cards.push(`<article><b>银行：后段样本仍有延续</b><p>招商 / 兴业训练期 ${esc(f(bank.train))}，留出期 ${esc(f(bank.holdout))}。样本中重复持有这类股票，可能重复承担较多共同波动。</p></article>`);
    }
    if (ev) {
      cards.push(`<article><b>新能源：不能把行业相同当作稳定关系</b><p>宁德 / 比亚迪训练期 ${esc(f(ev.train))}，留出期降到 ${esc(f(ev.holdout))}，近期60日回到 ${esc(f(ev.recent60))}。长窗口相关性会掩盖阶段变化。</p></article>`);
    }
    $('insights').innerHTML = cards.join('');

    if (result.adjustment_sensitivity) {
      $('sensitivity').textContent = '复权敏感性：同窗口前复权与后复权的120组全样本相关系数，最大绝对差为 ' + f(result.adjustment_sensitivity.max_absolute_delta) + '。主要强关联组合仍然存在；这只是同源口径对照，并非独立数据核验。';
    }

    if (liquor) selectedPair = [liquor.a, liquor.b];
    matrix();
    covariance();
    pairs();
    if (liquor) detail(liquor.a, liquor.b);
  } catch (e) {
    $('error').hidden = false;
    $('error').textContent = '无法载入分析：' + e.message;
  }
}

init();
