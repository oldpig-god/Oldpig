(()=>{'use strict';
const $=id=>document.getElementById(id),setup=$('strategy-setup'),toggle=$('config-toggle');
function expanded(open){setup.hidden=!open;toggle.setAttribute('aria-expanded',String(open));toggle.textContent=open?'收起实验配置':'配置新实验';}
toggle.onclick=()=>expanded(setup.hidden);document.addEventListener('strategy-rendered',()=>expanded(false));
const specs=[['performance','收益与风险',[$('nav-chart').closest('section')]],['validation','稳健性检验',[$('split-results').closest('section'),document.querySelector('.diagnostics')]],['execution','交易账本',[$('trades').closest('section')]],['evidence','方法与证据',[$('rule').closest('section')]]];
const tabs=document.createElement('div');tabs.className='report-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','实验报告内容');
const groups=[];
for(const [key,label,nodes] of specs){const group=document.createElement('div');group.id='report-panel-'+key;group.setAttribute('role','tabpanel');group.setAttribute('aria-labelledby','report-tab-'+key);nodes[0].before(group);nodes.forEach(n=>group.append(n));groups.push(group);const b=document.createElement('button');b.type='button';b.id='report-tab-'+key;b.setAttribute('role','tab');b.setAttribute('aria-controls',group.id);b.textContent=label;b.onclick=()=>select(key);tabs.append(b);}
$('metrics').after(tabs);
function select(key){groups.forEach(g=>g.hidden=g.id!=='report-panel-'+key);[...tabs.children].forEach(b=>{const active=b.id==='report-tab-'+key;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});}
tabs.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const buttons=[...tabs.children];let i=buttons.indexOf(document.activeElement);i=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[i].click();buttons[i].focus();};
select('performance');
})();
