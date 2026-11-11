'use strict';
const $=id=>document.getElementById(id),valid=n=>typeof n==='number'&&Number.isFinite(n);
const number=(n,d=2)=>valid(n)?n.toFixed(d):'—',percent=(n,d=1)=>valid(n)?`${n>0?'+':''}${n.toFixed(d)}%`:'—';
const tone=n=>n>0?'positive':n<0?'negative':'';
const labels={technical_price:'技术图价格',return_price:'收益口径',missing:'缺失与连续性',warmup:'指标初始化',macd:'MACD约定',volume:'量比定义',quote:'快照口径',levels:'高低点参考',event_study:'事件筛选',event_outcome:'观察结果',bias:'样本边界'};
const ruleNames={ma_cross:'MA20上穿MA60',breakout_volume:'突破前20日高点且量比≥1.5',rsi_enter70:'RSI首次进入70以上'};
let data,selected='002463.SZ',pollTimer,cursorIndex;
const quote=s=>data.quotes.find(q=>q.symbol===s&&q.status==='ok');
const asset=()=>data.assets.find(a=>a.symbol===selected);
function overview(){
 $('stock-rows').innerHTML=data.assets.map(a=>{const l=a.latest,q=quote(a.symbol);return `<tr data-symbol="${a.symbol}" class="${a.symbol===selected?'active':''}"><td><button data-select="${a.symbol}">${a.name}<small>${a.symbol}</small></button></td><td>${q?number(q.price):'—'}<small class="${tone(q?.pct_change)}">${q?percent(q.pct_change,2):'快照不可用'}</small></td><td>${number(l.raw_close)}<small>${l.date}</small></td><td><span class="badge">${a.trend}</span></td><td>${number(l.rsi14,1)}</td><td class="${tone(l.bias20_pct)}">${percent(l.bias20_pct)}</td><td>${number(l.atr_pct,1)}%</td><td>${number(l.volume_ratio20)}×</td><td class="${tone(l.excess63_pct)}">${percent(l.excess63_pct)}</td></tr>`;}).join('');
}
function selectedFacts(){
 const a=asset(),l=a.latest,q=quote(a.symbol);$('selected-name').textContent=a.name+' · '+a.symbol;
 $('daily-price').textContent=number(l.raw_close);$('daily-price').setAttribute('aria-label','完整日线收盘价');
 $('selected-quote').textContent=`完整日线 ${l.date}${q?'　｜　快照 '+number(q.price)+' '+percent(q.pct_change,2):''}`;
 const bias=q&&valid(l.ma20)?(q.price/l.ma20-1)*100:null;
 const momentum=l.macd>l.macd_signal?(l.macd>0?'DIF高于DEA，且在零轴上方':'DIF高于DEA，仍在零轴下方'):'DIF低于DEA';
 const facts=[['趋势',a.trend,`日线价相对MA20 ${percent(l.bias20_pct)}${bias!==null?'；快照相对'+l.date+'的MA20 '+percent(bias):''}。后者是静态比较，不是今日已重算的均线。`],
 ['动量','RSI14 '+number(l.rsi14,1),`${momentum}。${l.rsi14>=70?'强势区可以持续，不等于必然回落。':l.rsi14<30?'处在常用超卖观察区，不能单独判断底部。':'RSI处在30至70之间。'}`],
 ['量价','此前20日均量的 '+number(l.volume_ratio20)+' 倍',`本完整日线${l.close>l.prior_high20?'收盘高于':'未突破'}此前20日最高价。放量、上涨与突破需要分别核对。`],
 ['波动风险','ATR14 / 价格 '+number(l.atr_pct,1)+'%',`ATR约 ${number(l.atr14)} 个当前价格基准单位；近252日高点回撤 ${percent(l.drawdown252_pct)}。ATR不指示方向。`]];
 $('observations').innerHTML=facts.map(([title,value,text])=>`<article class="observation"><h3>${title}</h3><strong>${value}</strong><p>${text}</p></article>`).join('');
 $('levels').innerHTML=[['MA20 / MA60',number(l.ma20)+' / '+number(l.ma60)],['此前20日低 / 高',number(l.prior_low20)+' / '+number(l.prior_high20)],['此前60日低 / 高',number(l.prior_low60)+' / '+number(l.prior_high60)]].map(([label,value])=>`<div class="level-row"><span>${label}</span><b>${value}</b></div>`).join('');
}
const svg=(w,h,content)=>`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" aria-hidden="true">${content}</svg>`;
function line(rows,key,x,y,color,width=1.5){let path='',open=false;rows.forEach((r,i)=>{if(valid(r[key])){path+=(open?'L':'M')+x(i).toFixed(2)+','+y(r[key]).toFixed(2);open=true;}else open=false;});return `<path d="${path}" fill="none" stroke="${color}" stroke-width="${width}"/>`;}
function charts(){
 const all=data.series[selected],count=Number($('range').value),rows=count?all.slice(-count):all;
 const w=930,h=340,left=54,right=18,top=16,bottom=30,plot=w-left-right,x=i=>left+(i+.5)/rows.length*plot;
 const fields=['high','low','ma20','ma60','ma120',...($('bands').checked?['boll_upper','boll_lower']:[])];
 const values=rows.flatMap(r=>fields.map(k=>r[k]).filter(valid));let low=Math.min(...values),high=Math.max(...values);const pad=(high-low||1)*.07;low-=pad;high+=pad;
 const y=v=>top+(high-v)/(high-low)*(h-top-bottom);let parts='';
 for(let i=0;i<5;i++){const v=low+(high-low)*i/4,yy=y(v);parts+=`<line class="tick" x1="${left}" x2="${w-right}" y1="${yy}" y2="${yy}"/><text x="${left-8}" y="${yy+3}" text-anchor="end">${number(v,1)}</text>`;}
 if($('bands').checked){parts+=line(rows,'boll_upper',x,y,'#c7d3df',1)+line(rows,'boll_lower',x,y,'#c7d3df',1);}
 const candleWidth=Math.max(.5,Math.min(6,plot/rows.length*.65));
 rows.forEach((r,i)=>{if(!['open','close','high','low'].every(k=>valid(r[k])))return;const color=r.close>=r.open?'#b95e62':'#4e9787',xx=x(i);parts+=`<line x1="${xx}" x2="${xx}" y1="${y(r.high)}" y2="${y(r.low)}" stroke="${color}" stroke-width=".8"/><rect x="${xx-candleWidth/2}" y="${Math.min(y(r.open),y(r.close))}" width="${candleWidth}" height="${Math.max(.7,Math.abs(y(r.open)-y(r.close)))}" fill="${color}"/>`;});
 for(const [key,color] of [['ma20','#c29453'],['ma60','#527da0'],['ma120','#9775a5']])parts+=line(rows,key,x,y,color);
 for(const i of [0,Math.floor(rows.length/2),rows.length-1])parts+=`<text x="${x(i)}" y="${h-8}" text-anchor="${i===0?'start':i===rows.length-1?'end':'middle'}">${rows[i].date}</text>`;
 parts+=`<line id="cursor" class="cursor" x1="0" x2="0" y1="${top}" y2="${h-bottom}" visibility="hidden"/>`;
 $('price-chart').innerHTML=svg(w,h,parts);
 const chartSvg=$('price-chart').querySelector('svg');chartSvg.removeAttribute('aria-hidden');chartSvg.setAttribute('tabindex','0');chartSvg.setAttribute('role','img');chartSvg.setAttribute('aria-label',asset().name+'完整日线与均线；左右方向键查看日期');
 function inspect(i){cursorIndex=Math.max(0,Math.min(rows.length-1,i));const r=rows[cursorIndex],cross=$('cursor');cross.setAttribute('x1',x(cursorIndex));cross.setAttribute('x2',x(cursorIndex));cross.setAttribute('visibility','visible');$('chart-readout').textContent=`${r.date}　参考收 ${number(r.close)}　实际收 ${number(r.raw_close)}　MA20 ${number(r.ma20)}　RSI ${number(r.rsi14,1)}　ATR ${number(r.atr14)}　量比 ${number(r.volume_ratio20)}×`;}
 chartSvg.addEventListener('pointermove',e=>{const box=chartSvg.getBoundingClientRect(),xx=(e.clientX-box.left)/box.width*w;inspect(Math.floor((xx-left)/plot*rows.length));});
 chartSvg.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();inspect(cursorIndex+(e.key==='ArrowLeft'?-1:1));}});inspect(rows.length-1);
 const vh=88,vmax=Math.max(2,...rows.map(r=>r.volume_ratio20).filter(valid))*1.05,vy=v=>vh-17-v/vmax*(vh-33);
 let volume=`<text x="${left}" y="13">量 / 前20日均量</text><line x1="${left}" x2="${w-right}" y1="${vy(1)}" y2="${vy(1)}" stroke="#aebdcc" stroke-dasharray="4 3"/><text x="${left-8}" y="${vy(1)+3}" text-anchor="end">1×</text>`;
 rows.forEach((r,i)=>{if(valid(r.volume_ratio20))volume+=`<rect x="${x(i)-candleWidth/2}" y="${vy(r.volume_ratio20)}" width="${candleWidth}" height="${vy(0)-vy(r.volume_ratio20)}" fill="${r.close>=r.open?'#c28b8b':'#84b1a5'}"/>`;});$('volume-chart').innerHTML=svg(w,vh,volume);
 const mh=190,max=Math.max(.01,...rows.flatMap(r=>['macd','macd_signal','macd_hist'].map(k=>Math.abs(r[k]??0)))),my=v=>54-v/max*35,ry=v=>174-v/100*65;
 let momentum=`<text x="${left}" y="12">MACD 12/26/9 · 柱×1</text><line class="tick" x1="${left}" x2="${w-right}" y1="54" y2="54"/><text x="${left}" y="106">RSI 14</text>`;
 for(const level of [30,70])momentum+=`<line x1="${left}" x2="${w-right}" y1="${ry(level)}" y2="${ry(level)}" stroke="#d7e1ea" stroke-dasharray="4 3"/><text x="${left-8}" y="${ry(level)+3}" text-anchor="end">${level}</text>`;
 rows.forEach((r,i)=>{if(valid(r.macd_hist))momentum+=`<rect x="${x(i)-candleWidth/2}" y="${Math.min(my(0),my(r.macd_hist))}" width="${candleWidth}" height="${Math.max(.5,Math.abs(my(r.macd_hist)-my(0)))}" fill="${r.macd_hist>=0?'#cc9698':'#91b6ab'}"/>`;});
 momentum+=line(rows,'macd',x,my,'#527da0')+line(rows,'macd_signal',x,my,'#c29453')+line(rows,'rsi14',x,ry,'#8375a2');$('momentum-chart').innerHTML=svg(w,mh,momentum);
}
function eventTable(){const period=$('period').value,stats=data.event_statistics.filter(r=>r.symbol===selected&&r.period===period),dates=data.dates;
 $('study-dates').textContent=`形成期 ${dates.train_start} — ${dates.train_end}；留出期 ${dates.holdout_start} — ${dates.holdout_end}。表格为${asset().name}，持有观察窗口20个交易日。`;
 $('event-stats').innerHTML=stats.map(r=>`<tr><td>${r.label}</td><td>${r.n}</td><td class="${tone(r.mean_excess_pct)}">${percent(r.mean_excess_pct)}</td><td>${percent(r.median_excess_pct)}</td><td>${valid(r.outperform_rate)?number(r.outperform_rate*100,0)+'%':'—'}</td><td>${percent(r.baseline_mean_excess_pct)}<small>${r.baseline_n}个普通起点</small></td><td><span class="badge ${r.low_sample?'low':''}">${r.low_sample?'不足10次 · 仅描述':'未作显著性检验'}</span><small>边界/未到期${r.pending_or_boundary} · 缺失排除${r.missing_outcomes}</small></td></tr>`).join('');
 const events=data.events.filter(r=>r.symbol===selected&&r.period===period).sort((a,b)=>b.date.localeCompare(a.date));$('event-log').innerHTML=events.length?events.map(r=>`<tr><td>${ruleNames[r.rule]}</td><td>${r.date}</td><td>${r.end_date}</td><td>${percent(r.stock_pct)}</td><td>${percent(r.benchmark_pct)}</td><td>${percent(r.excess_pct)}</td></tr>`).join(''):'<tr><td colspan="6">该期间没有已完成且满足数据条件的事件。</td></tr>';
}
function coColor(v){const target=v>=0?[183,87,96]:[54,102,145],base=[241,245,249];return `rgb(${base.map((b,i)=>Math.round(b+(target[i]-b)*Math.abs(v))).join(',')})`;}
function comovement(){const key=$('correlation').value,m=data.comovement[key],symbols=Object.keys(data.watchlist);
 $('heatmap').innerHTML=`<thead><tr><th>相关系数 r</th>${symbols.map(s=>`<th>${data.watchlist[s]}</th>`).join('')}</tr></thead><tbody>${symbols.map(a=>`<tr><th>${data.watchlist[a]}</th>${symbols.map(b=>{const v=m[a][b];return `<td><button data-a="${a}" data-b="${b}" style="background:${coColor(v)};color:${Math.abs(v)>.7?'white':'#3c546b'}" aria-label="${data.watchlist[a]}与${data.watchlist[b]} ${number(v,3)}">${number(v)}</button></td>`;}).join('')}</tr>`).join('')}</tbody>`;
 const first=symbols.slice(0,5),within=first.flatMap((a,i)=>first.slice(i+1).map(b=>m[a][b])),sany=first.map(s=>m['600031.SH'][s]),mean=x=>x.reduce((a,b)=>a+b,0)/x.length;
 $('co-summary').textContent=`当前口径：前五只股票之间的平均相关性 ${number(mean(within),3)}；三一重工与这五只的平均相关性 ${number(mean(sany),3)}。分散持有多只证券，不一定分散了共同波动。`;
 $('co-dates').textContent=`收益率共同样本 ${data.dates.return_observations}日；最近60日从 ${data.dates.recent60_start} 开始。前后复权全样本相关系数最大差 ${number(data.comovement.qfq_hfq_max_correlation_delta,3)}。`;
}
function select(symbol){selected=symbol;overview();selectedFacts();charts();eventTable();}
function render(){const quotes=data.quotes.filter(q=>q.status==='ok').map(q=>q.event_time).sort();$('daily-asof').textContent='完整日线截至 '+(data.daily_asof||data.expected_end);$('quote-asof').textContent=quotes.length?'已保存快照 '+quotes.at(-1).replace('T',' ').slice(0,19):'当前没有可用行情快照';$('generated').textContent='研究生成于 '+new Date(data.generated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（上海）';
 $('method-list').innerHTML=Object.entries(data.methods).map(([key,value])=>`<dt>${labels[key]||key}</dt><dd>${value}</dd>`).join('');$('references').innerHTML=data.references.map(r=>`<a href="${r.url}" target="_blank" rel="noopener noreferrer">${r.name} ↗</a>`).join('');select(selected);comovement();}
async function load(){const r=await fetch('/api/technical',{cache:'no-store'});if(!r.ok)throw Error('研究数据尚未生成，请运行 python -m app.technical');data=await r.json();render();}
function error(message){$('error').hidden=false;$('error').textContent=message;}
async function poll(){try{const r=await fetch('/api/technical/status'),state=await r.json();$('refresh').disabled=state.running;$('job-status').textContent=state.running?'正在采集与重算，保留上一份结果…':state.error?'更新失败，旧结果保留':'研究结果已保存';if(!state.running){clearInterval(pollTimer);if(state.error)error(state.error);else await load();}}catch(e){clearInterval(pollTimer);$('refresh').disabled=false;error(e.message);}}
async function refresh(){try{$('error').hidden=true;$('refresh').disabled=true;const r=await fetch('/api/technical/refresh',{method:'POST',headers:{'x-workbench-action':'local-refresh'}});if(!r.ok)throw Error((await r.json()).detail||'更新失败');pollTimer=setInterval(poll,2000);await poll();}catch(e){$('refresh').disabled=false;error(e.message);}}
$('stock-rows').addEventListener('click',e=>{const row=e.target.closest('[data-symbol]');if(row)select(row.dataset.symbol);});
$('range').addEventListener('change',charts);$('bands').addEventListener('change',charts);$('period').addEventListener('change',eventTable);$('correlation').addEventListener('change',comovement);$('refresh').addEventListener('click',refresh);
$('heatmap').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const m=data.comovement[$('correlation').value];$('co-selected').textContent=`${data.watchlist[b.dataset.a]} / ${data.watchlist[b.dataset.b]}：${number(m[b.dataset.a][b.dataset.b],3)}。${b.dataset.a===b.dataset.b?'对角线是自身相关性。':'同期相关不表示存在领先关系。'}`;});
load().catch(e=>error(e.message));
