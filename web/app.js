'use strict';
const $=s=>document.querySelector(s);let overview,records=[],currentRows=[],activeSeries=[];let wikiPages=[],wikiCatalog=null,wikiRequest=0;
const el=(tag,text,cls)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x};
async function api(path){const r=await fetch(path);if(!r.ok){let b=await r.json().catch(()=>({}));throw Error(b.detail||`HTTP ${r.status}`)}return r.json()}
function error(e){$('#error').textContent=e.message;$('#error').hidden=false;setTimeout(()=>$('#error').hidden=true,8000)}
function link(text,href){const a=el('a',text);a.href=href;return a}
function showPage(sid,n){location.hash=`page/${encodeURIComponent(sid)}/${n}`}
function sourceName(sid){return overview?.sources.find(s=>s.id===sid)?.name||sid}
function options(select,items){select.replaceChildren(...items.map(([value,label])=>{const o=el('option',label);o.value=value;return o}))}
function route(){const hash=location.hash.slice(1)||'overview';const name=hash.split('/')[0];document.querySelectorAll('main>section').forEach(s=>s.hidden=s.id!==name);document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.hash===`#${name}`));if(name==='wiki'){const rest=hash.startsWith('wiki/')?hash.slice(5):'catalog.md';const [path,params]=rest.split('?');loadWiki(decodeURIComponent(path),new URLSearchParams(params||'').get('anchor')).catch(error)}if(name==='page'){const [,sid,n]=hash.split('/');loadPage(decodeURIComponent(sid),Number(n)).catch(error)}}
async function init(){overview=await api('/api/overview');records=await api('/api/observations');$('#stats').replaceChildren(...[[overview.sources.length,'Документов в коллекции'],[overview.pages.toLocaleString('ru'),'Страниц извлечено'],[overview.observations,'Значений сверено с PDF']].map(([n,t])=>{const c=el('div',undefined,'card');c.append(el('strong',n),el('span',t));return c}));
$('#sources-list').replaceChildren(...overview.sources.map(s=>{const c=el('div',undefined,'result');const h=el('h3');h.append(link(s.name,s.url));c.append(h,el('p',`Год: ${s.report_year||'не подтверждён'} · ${s.extracted_pages||0}/${s.pages||'?'} страниц · ${s.status}`),el('small',`Подлинность: ${s.authenticity}. ${s.agency}`));if(s.local_available){const b=el('button','Читать страницы');b.onclick=()=>showPage(s.id,1);c.append(el('br'),b);const q=overview.summaries.find(q=>q.source_id===s.id);if(q)c.append(el('p',`OCR: ${q.ocr_pages}; таблиц-кандидатов: ${q.tables}; страниц с предупреждениями: ${q.review_pages}; ошибок: ${q.failed_pages.length}.`))}return c}));
wikiPages=await api('/api/wiki');wikiCatalog=await api('/api/wiki/catalog');renderWikiNavigation();if(!location.hash.startsWith('#wiki'))await loadWiki('index.md');
options($('#territory'),[['','Все территории'],...[...new Set(records.map(r=>r.territory))].map(x=>[x,x])]);$('#territory').onchange=selectIndicator;$('#indicator').onchange=()=>drawData().catch(error);const years=[...new Set(records.map(r=>r.year))].sort((a,b)=>a-b);for(const id of ['#year-from','#year-to']){options($(id),[['','Без ограничения'],...years.map(y=>[y,y])]);$(id).onchange=()=>drawData().catch(error)}selectIndicator();route()}
function selectIndicator(){const allowed=records.filter(r=>!$('#territory').value||r.territory===$('#territory').value);const map=new Map(allowed.map(r=>[r.comparability_group,`${r.indicator} · ${r.territory}`]));options($('#indicator'),[...map]);drawData().catch(error)}
function inYearRange(r){return (!$('#year-from').value||r.year>=Number($('#year-from').value))&&(!$('#year-to').value||r.year<=Number($('#year-to').value))}
async function drawData(){const group=$('#indicator').value;currentRows=records.filter(r=>r.comparability_group===group&&inYearRange(r)).sort((a,b)=>a.year-b.year);$('#data-rows').replaceChildren(...currentRows.map(r=>{const tr=el('tr');tr.append(el('td',r.year),el('td',r.raw_value),el('td',r.unit),el('td',`${r.confidence} · сверено AI-агентом`));const cell=el('td');const a=link(`PDF стр. ${r.page} · ${r.table}`,`#page/${r.source_id}/${r.page}`);a.title=sourceName(r.source_id);cell.append(a);tr.append(cell);return tr}));$('#chart-title').textContent=currentRows[0]?.indicator||'Нет данных';$('#chart').replaceChildren();activeSeries=[];
try{if($('#year-from').value&&$('#year-to').value&&Number($('#year-from').value)>Number($('#year-to').value))throw Error('Начальный год больше конечного.');const rows=(await api(`/api/series?group=${encodeURIComponent(group)}`)).filter(inYearRange);if(!rows.length)throw Error('В выбранном диапазоне лет нет значений.');if(group!==$('#indicator').value)return;activeSeries=rows;plot(rows);$('#chart-note').textContent=`${rows[0].unit}. ${rows[0].comparability_note} Источник: ${sourceName(rows[0].source_id)}, стр. ${rows[0].page}.`;}
catch(e){$('#chart-note').textContent=e.message;}
for(const id of ['#year-a','#year-b']){options($(id),activeSeries.map(r=>[r.year,r.year]));$(id).disabled=!activeSeries.length;$(id).onchange=compare}
if(activeSeries.length)$('#year-b').value=activeSeries.at(-1).year;compare()}
function compare(){const a=activeSeries.find(r=>r.year==$('#year-a').value),b=activeSeries.find(r=>r.year==$('#year-b').value);if(!a||!b){$('#comparison').textContent='Сравнение недоступно до подтверждения сопоставимости.';return}const diff=Number(b.value)-Number(a.value);const pct=Number(a.value)?diff/Number(a.value)*100:null;$('#comparison').textContent=`Изменение ${a.year} → ${b.year}: ${diff.toLocaleString('ru',{maximumFractionDigits:4})} ${a.unit}${pct===null?'':` (${pct.toFixed(1)}%)`}.`}
function plot(rows){const svg=$('#chart'),ns='http://www.w3.org/2000/svg',W=900,H=300,pad=55;const val=rows.map(r=>Number(r.value));const max=Math.max(...val)*1.12||1;const x=i=>pad+i*(W-2*pad)/Math.max(rows.length-1,1),y=v=>H-pad-v*(H-2*pad)/max;function node(tag,attrs,text){const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text)e.textContent=text;svg.append(e);return e}
for(let i=0;i<=4;i++){const v=max*i/4;node('line',{x1:pad,x2:W-pad,y1:y(v),y2:y(v),stroke:'#d9e4e3'});node('text',{x:pad-10,y:y(v)+4,'text-anchor':'end'},v.toLocaleString('ru',{maximumFractionDigits:2}))}
node('polyline',{points:rows.map((r,i)=>`${x(i)},${y(Number(r.value))}`).join(' '),fill:'none',stroke:'#137e96','stroke-width':3});rows.forEach((r,i)=>{node('text',{x:x(i),y:H-20,'text-anchor':'middle'},String(r.year));const c=node('circle',{cx:x(i),cy:y(Number(r.value)),r:6,fill:'#137e96',tabindex:0,role:'button','aria-label':`${r.year}: ${r.value} ${r.unit}; открыть источник`});const t=document.createElementNS(ns,'title');t.textContent=`${r.year}: ${r.raw_value} ${r.unit} · нажмите для просмотра источника`;c.append(t);c.onclick=()=>showPage(r.source_id,r.page);c.onkeydown=e=>{if(e.key==='Enter')showPage(r.source_id,r.page)}})}
function inline(text,container,current){const rx=/\[([^\]]+)\]\(([^)]+)\)/g;let end=0,m;while((m=rx.exec(text))){container.append(document.createTextNode(text.slice(end,m.index)));let dest=m[2];const a=el('a',m[1]);if(/^https?:\/\//.test(dest)){a.href=dest;a.target='_blank';a.rel='noopener noreferrer'}else{a.href='#wiki';a.onclick=e=>{e.preventDefault();const u=new URL(dest,`https://local.invalid/${current}`);const next=u.pathname.slice(1);const src=next.match(/^sources\/((?:drive|official)-[\w-]+)\.md$/),page=u.hash.match(/^#page-(\d+)$/);if(src&&page&&overview?.sources.some(s=>s.id===src[1]&&s.local_available)){showPage(src[1],Number(page[1]));return}if(next.startsWith('sources/')||next.startsWith('topics/')||next.startsWith('entities/')||next.startsWith('syntheses/')||next.startsWith('queries/')||['index.md','catalog.md','LOG.md'].includes(next)){navigateWiki(next,u.hash.slice(1))}else error(Error('Этот файл доступен в репозитории.'))}}container.append(a);end=rx.lastIndex}container.append(document.createTextNode(text.slice(end)))}
async function loadWiki(path,anchor){
 const request=++wikiRequest;const r=await fetch(`/api/wiki/${path.split('/').map(encodeURIComponent).join('/')}`);if(!r.ok)throw Error('Статья не найдена');
 const text=await r.text();if(request!==wikiRequest)return;const target=$('#wiki-content');target.replaceChildren();target.dataset.path=path;let pre=null,table=null,list=null;
 for(const line of text.split('\n')){
  if(line.startsWith('```')){if(pre)pre=null;else{pre=el('pre');target.append(pre)}continue}
  if(pre){pre.textContent+=line+'\n';continue}
  const a=line.match(/^<a id="([^"]+)"><\/a>$/);if(a){const span=el('span');span.id=a[1];target.append(span);continue}
  if(line.startsWith('<!--'))continue;
  if(line.trim().startsWith('|')){
   const cells=line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
   if(cells.every(c=>/^:?-{3,}:?$/.test(c)))continue;
   const header=!table;if(!table){const wrap=el('div',undefined,'table-scroll');table=el('table');wrap.append(table);target.append(wrap)}
   const row=el('tr');for(const cell of cells){const c=el(header?'th':'td');inline(cell,c,path);row.append(c)}table.append(row);list=null;continue
  }table=null;
  if(/^[-*] /.test(line)){if(!list){list=el('ul');target.append(list)}const item=el('li');inline(line.slice(2),item,path);list.append(item);continue}list=null;
  if(!line.trim())continue;
  const h=line.match(/^(#{1,3})\s+(.*)/);const e=el(h?'h'+Math.min(h[1].length+1,4):line.startsWith('> ')?'blockquote':'p');inline(h?h[2]:line.replace(/^> /,''),e,path);target.append(e)
 }
 if(anchor)document.getElementById(anchor)?.scrollIntoView();else target.scrollTop=0;
}

function navigateWiki(path,anchor){
 const dest=`wiki/${path}${anchor?'?anchor='+encodeURIComponent(anchor):''}`;
 if(location.hash.slice(1)===dest)loadWiki(path,anchor).catch(error);else location.hash=dest;
}
function articleButton(path,title){const b=el('button',title);b.dataset.path=path;b.onclick=()=>navigateWiki(path);return b}
function renderWikiNavigation(){
 const target=$('#wiki-list');target.replaceChildren();
 target.append(articleButton('catalog.md','Тематический каталог'));
 const grouped=new Set();
 for(const section of wikiCatalog.sections){
  const group=el('details',undefined,'wiki-group');group.open=true;group.append(el('summary',section.title));
  for(const sub of section.subtopics){
   const box=el('div',undefined,'wiki-subtopic');box.append(el('h3',sub.title));
   for(const topic of sub.topics){
    if(topic.article){box.append(articleButton(topic.article,topic.title));grouped.add(topic.article)}
    else box.append(el('p',topic.title+' · в очереди','wiki-pending'));
   }group.append(box);
  }target.append(group);
 }
 const support=el('details',undefined,'wiki-group');support.append(el('summary','Источники и справочные страницы'));
 for(const page of wikiPages){if(!grouped.has(page.path)&&page.path!=='catalog.md')support.append(articleButton(page.path,page.title))}target.append(support);
 $('#wiki-search-status').textContent='Разделы → подтемы → статьи. Очередь тем видна в каталоге.';
}
let wikiSearchTimer,wikiSearchRequest=0;
$('#wiki-query').oninput=()=>{
 clearTimeout(wikiSearchTimer);const request=++wikiSearchRequest,q=$('#wiki-query').value.trim();
 if(!q){renderWikiNavigation();return}
 wikiSearchTimer=setTimeout(async()=>{try{
  const rows=await api(`/api/wiki/search?q=${encodeURIComponent(q)}`);if(request!==wikiSearchRequest)return;
  $('#wiki-search-status').textContent=`Найдено статей: ${rows.length}`;
  $('#wiki-list').replaceChildren(...rows.map(r=>{const d=el('div',undefined,'wiki-hit');d.append(articleButton(r.path,r.title),el('small',r.section+' / '+r.subtopic),el('p',r.snippet));return d}));
 }catch(e){if(request===wikiSearchRequest)error(e)}},200);
};

$('#search-form').onsubmit=async e=>{e.preventDefault();try{const rows=await api(`/api/search?q=${encodeURIComponent($('#query').value)}`);$('#search-results').replaceChildren(...rows.map(r=>{const d=el('div',undefined,'result');const h=el('h3');h.append(link(`${sourceName(r.source_id)} · стр. ${r.page}`,`#page/${r.source_id}/${r.page}`));d.append(h,el('p',r.snippet));return d}));if(!rows.length)$('#search-results').textContent='Совпадений нет в обработанных страницах.'}catch(e){error(e)}};
async function loadPage(sid,n){const p=await api(`/api/documents/${encodeURIComponent(sid)}/pages/${n}`);$('#page-title').textContent=`${p.source.name} · страница ${n}`;$('#page-meta').textContent=`Метод: ${p.method} · ${[...(p.warnings||[]),...(p.table_audit?.requires_review?['Аудит таблиц: требуется проверка возможных пропусков']:[])].join('; ')||'нет автоматических предупреждений'} · подлинность: ${p.source.authenticity}`;$('#page-text').textContent=p.text||p.error;$('#page-links').replaceChildren(link('Оригинал PDF',`/api/documents/${sid}/pdf#page=${n}`),link('Сайт источника',p.source.url));if(n>1)$('#page-links').append(link('← Предыдущая',`#page/${sid}/${n-1}`));if(n<p.source.pages)$('#page-links').append(link('Следующая →',`#page/${sid}/${n+1}`));$('#page-preview').src=`/api/documents/${sid}/preview/${n}`;$('#page-preview').onerror=()=>{$('#page-preview').alt='Превью не создано; откройте оригинальный PDF.'};$('#page-tables').replaceChildren(...(p.tables||[]).map((t,i)=>{const wrap=el('div',undefined,'table-scroll');const table=el('table');const audit=p.table_audit?.tables.find(a=>a.table_index===i);table.append(el('caption',`${t.method} · ${t.status} · ${audit?.flags.length?'Возможная потеря данных: '+audit.flags.join(', '):audit?'Автоматический аудит не обнаружил потерь; нужна сверка с PDF':'Аудит не выполнен'}`));if(audit?.missing_numeric_tokens.length)wrap.append(el('p','Числа страницы вне распознанной сетки: '+audit.missing_numeric_tokens.join(', '),'notice'));for(const row of t.rows){const tr=el('tr');for(const cell of row)tr.append(el('td',cell??''));table.append(tr)}wrap.append(table);return wrap}))}
$('#page-back').onclick=()=>history.back();window.addEventListener('hashchange',route);init().catch(error);
