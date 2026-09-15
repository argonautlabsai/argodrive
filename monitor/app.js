import {clusterView} from '/cluster-view.js';
import {engramMonitorView} from '/engram-monitor-view.js';
import {topologySVG,topologyInspector,topologyConnections} from '/topology-view.js';
import {finite,fmt,length,runId,escapeHTML as esc,flatten,compare,gain,settingsDiff,filterRuns,chartPath,sharedReadBarScale,monitorReadBarScale,readAutoScale,readWindowStats,runExportOptions,runExportURL} from '/app-model.js';

const paths={overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',live:'M2 12h4l3-8 5 16 3-8h5',runs:'M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2h-3 M9 2h6v4H9z M7 11h10 M7 16h7',compare:'M7 3v18 M3 7l4-4 4 4 M17 21V3 M13 17l4 4 4-4',diagnostics:'M4 21v-6 M4 9V3 M12 21v-9 M12 6V3 M20 21v-3 M20 12V3 M1 9h6 M9 12h6 M17 18h6',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',refresh:'M20 7V3l-4 4 M20 7a9 9 0 1 0 1 8 M20 7h-5',sun:'M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',moon:'M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11',arrow:'M4 12h16 M14 6l6 6-6 6',download:'M12 3v12 M7 10l5 5 5-5 M4 15v5h16v-5',clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',bolt:'M13 2 4 14h7l-1 8 10-12h-7z',disk:'M5 3h14v18H5z M8 7h8 M8 11h8 M15 17h1',check:'M5 12l4 4L19 6',info:'M12 11v6 M12 7v.1 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',folder:'M3 5h6l2 2h10v13H3z',search:'M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14 M15 15l6 6',close:'M6 6l12 12 M6 18 18 6',memory:'M4 6h16v12H4z M8 9v6 M12 9v6 M16 9v6 M7 3v3 M12 3v3 M17 3v3 M7 18v3 M12 18v3 M17 18v3',pause:'M8 4v16 M16 4v16',play:'m7 3 14 9-14 9z',file:'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6'};
const icon=(name,cls='')=>`<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.info}"/></svg>`;
const $=s=>document.querySelector(s);
paths.monitor=paths.live;paths.runmonitor=paths.live;paths.cluster=paths.memory;paths.ssds=paths.disk;paths.engram=paths.memory;paths.streaming=paths.settings;paths.topology='M8 4h8v5H8z M3 16h6v5H3z M15 16h6v5h-6z M12 9v4 M6 16v-3h12v3';paths.benchmark='M4 19V5 M4 19h16 M8 16v-5 M12 16V7 M16 16v-9 M20 16V3';
const names={overview:'Overview',benchmark:'Benchmark',monitor:'Monitor',engram:'Monitor Engram',ssds:'SSDs',topology:'Topology',cluster:'Cluster',streaming:'Streaming',runs:'Runs',compare:'Compare',diagnostics:'Diagnostics',settings:'Settings'};
const colors=['var(--blue)','var(--teal)','var(--orange)','var(--purple)','var(--accent)'];
const devColor=(dev,i=0)=>({internal:colors[0],Green:colors[1],White:colors[2],Yellow:colors[3],Blue:colors[4]}[dev]||colors[i%colors.length]);
const native=window.webkit?.messageHandlers?.argodrive;
const state={rows:[],data:null,settings:null,route:'overview',selected:new Set(),baseline:'',candidate:'',filters:{},page:0,detail:new Map(),paused:false,liveWindow:20,monitorCombined:true,monitorAdvanced:false,monitorShowSSD:true,monitorShowEngram:false,monitorView:'storage',monitorSource:'latest',monitorSourceSet:false,monitorCursor:null,monitorEpoch:0,diagnostic:'reads',trace:'',expert:'',loadVersion:0,connected:false,ssdWindow:120,ssdFilter:'connected',ssdScale:'shared',streamingRun:'',streamingView:'engine',topology:null,topologySelected:'mac',topologyEmpty:true,topologyZoom:'fit',topologyLoading:false};
let toastTimer;
const badge=(text,kind='')=>`<span class="badge ${kind}">${esc(text)}</span>`;
const val=(x,d=2)=>esc(fmt(x,d));
const unit=(x,u,d=2)=>`${val(x,d)} <span class="unit">${esc(u)}</span>`;
const findRun=id=>state.rows.find(r=>runId(r)===id);
const shortModel=r=>(r?.model||'Model not recorded').replace(/-RoutedQ4K$/,'');
const date=r=>String(r?.ran||'Time not recorded').replace(/^\d{4}-/, '').replace(/:\d\d$/, '');
const safeStore=(k,v)=>{try{localStorage.setItem(k,v);}catch{}};
try{state.monitorCombined=localStorage.getItem('argodrive-monitor-combined')!=='false';}catch{}
try{state.monitorAdvanced=localStorage.getItem('argodrive-monitor-advanced')==='true';}catch{}
try{state.monitorView=localStorage.getItem('argodrive-monitor-view')||'storage';}catch{}
try{state.monitorShowSSD=localStorage.getItem('argodrive-monitor-show-ssd')!=='false';}catch{}
try{state.monitorShowEngram=localStorage.getItem('argodrive-monitor-show-engram')==='true'||state.monitorView==='engram';}catch{}
let theme='system';try{theme=localStorage.getItem('argodrive-theme')||'system';}catch{}
function applyTheme(value){theme=value;safeStore('argodrive-theme',value);document.documentElement.dataset.theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;$('#theme-toggle').innerHTML=icon(document.documentElement.dataset.theme==='dark'?'sun':'moon');}
applyTheme(theme);matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(theme==='system')applyTheme(theme);});
$('#theme-toggle').onclick=()=>applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');
document.querySelectorAll('[data-icon]').forEach(n=>n.innerHTML=icon(n.dataset.icon));
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
function notice(text=''){$('#notice').textContent=text;$('#notice').hidden=!text;}
function spotlightStartupNotice(data){
  const rows=data?.spotlight?.volumes||[];
  const enabled=rows.filter(r=>r.state==='enabled');
  if(!enabled.length)return '';
  const labels=enabled.map(r=>r.label||r.path).slice(0,4).join(', ');
  const suffix=enabled.length>4?` and ${enabled.length-4} more`:'';
  return `Spotlight indexing is enabled on ${labels}${suffix}. It may add background disk I/O and affect DeepSeek or SSD benchmark results. Review SSDs → Spotlight indexing before testing.`;
}
async function api(path,options={}){const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(60000),...options});let json;try{json=await response.json();}catch{throw Error('The local server returned an unreadable response.');}if(!response.ok||json.error)throw Error(json.error||`Request failed (${response.status})`);return json;}
function download(name,text,type='text/plain'){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
function heading(title,description,actions=''){return `<div class="page-heading"><div><div class="eyebrow">PERFORMANCE WORKSPACE</div><h1>${title}</h1><p>${description}</p></div>${actions?`<div class="button-group">${actions}</div>`:''}</div>`;}
const button=(label,action,style='',sym='')=>`<button class="button ${style}" data-action="${action}">${sym?icon(sym):''}${label}</button>`;
const linkButton=(label,route,style='',sym='')=>`<a class="button ${style}" href="#${route}">${sym?icon(sym):''}${label}</a>`;
function metric(label,value,note,sym='bolt',featured=false){return `<div class="metric ${featured?'featured':''}"><div class="metric-label">${icon(sym)}${label}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></div>`;}
const empty=(title,description,action='',sym='folder')=>`<div class="empty-state">${icon(sym)}<h2>${title}</h2><p>${description}</p>${action}</div>`;
function panel(title,subtitle,body,action=''){return `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div>${action}</div>${body}</section>`;}
function updateChrome(){
  // SSD throughput and Engram attribution are modes of the Monitor workspace,
  // rather than separate destinations. Keep their old hashes working below.
  $('#navigation').innerHTML=Object.entries(names).filter(([k])=>!['settings','ssds','engram'].includes(k)).map(([k,n])=>`<a class="nav-item ${state.route===k?'active':''}" href="#${k}" title="${n}" ${state.route===k?'aria-current="page"':''}>${icon(k)}<span class="nav-text">${n}</span>${k==='runs'?`<span class="nav-count">${state.rows.length}</span>`:''}</a>`).join('');
  $('.settings-link').classList.toggle('active',state.route==='settings');$('#breadcrumb').textContent=names[state.route];document.title=`ARGODRIVE · ${names[state.route]}`;
  $('#sidebar-mode').textContent=state.data?.mode==='reports'?'Runner feeds + saved reports':'Local hardware monitor';
  $('#connection').innerHTML=state.connected?`<span class="status-dot"></span>${state.data?.mode==='reports'?'Runner feeds connected':'Server connected'}`:'Server disconnected';
}
async function refresh(){
  $('#refresh').disabled=true;
  try{const [data,stats,settings]=await Promise.all([api('/data'),api('/stats'),api('/settings')]);state.data=data;captureReadOps(data);state.rows=flatten(stats.blocks);state.settings=settings;state.detail.clear();state.connected=true;notice(spotlightStartupNotice(data));state.selected=new Set([...state.selected].filter(id=>findRun(id)));
    // A live backend should open on the actual hardware charts. The runner
    // feed remains an explicit choice for completed/past test inspection.
    if(data.mode==='live'&&!state.monitorSourceSet&&state.monitorSource==='latest')state.monitorSource='hardware';
    render();}
  catch(e){state.connected=false;notice(`Could not refresh: ${e.message} ${state.rows.length?'Previously loaded reports are still shown.':''}`);if(!state.data)$('#main').innerHTML=empty('The local server is unavailable','Start ARGODRIVE, then refresh this page.',button('Try again','refresh','primary','refresh'));updateChrome();}
  finally{$('#refresh').disabled=false;}
}
$('#refresh').onclick=refresh;
function navigate(){
  let next=location.hash.slice(1).split('?')[0]||'overview';
  if(next==='live'||next==='runmonitor'){state.monitorSource=next==='live'?'hardware':'latest';state.monitorSourceSet=true;next='monitor';history.replaceState(null,'','#monitor');}
  else if(next==='engram'){state.monitorShowEngram=true;safeStore('argodrive-monitor-show-engram','true');next='monitor';history.replaceState(null,'','#monitor');}
  else if(next==='ssds'){state.monitorShowSSD=true;safeStore('argodrive-monitor-show-ssd','true');state.monitorSource='hardware';state.monitorSourceSet=true;next='monitor';history.replaceState(null,'','#monitor');}
  state.route=Object.hasOwn(names,next)?next:'overview';
  if(next==='streaming'){const requested=new URLSearchParams(location.hash.split('?')[1]||'').get('run');if(requested){state.streamingRun=requested;state.streamingView='recorded';}}
  render();
}
window.addEventListener('hashchange',navigate);
function render(){
  state.loadVersion++;updateChrome();
  if(!state.data)return;
  const pages={overview:renderOverview,benchmark:renderBenchmark,monitor:renderMonitor,engram:renderEngram,ssds:renderSSDs,topology:renderTopology,cluster:renderCluster,streaming:renderStreaming,runs:renderRuns,compare:renderCompare,diagnostics:renderDiagnostics,settings:renderSettings};
  pages[state.route]();
}
function renderEngram(){
  const d=state.data;
  $('#main').innerHTML=heading('Monitor Engram','Separate Engram row traffic from weight streaming, with the evidence source shown beside every chart.',button('Refresh','refresh','','refresh'))+engramMonitorView(d,{connected:state.connected,window:state.liveWindow});
}
async function renderCluster(){
  const version=state.loadVersion;
  $('#main').innerHTML=heading('Cluster','Loading saved wire qualification evidence…');
  try{const evidence=await api('/cluster');if(state.route!=='cluster'||version!==state.loadVersion)return;state.cluster=evidence;$('#main').innerHTML=panel('Monitor a GLM test','Follow the harness files while a run is active, or inspect its final SSD charts.',`<div class="panel-body">${linkButton('Open monitor','monitor','primary','live')}</div>`)+clusterView(evidence);}
  catch(e){if(state.route==='cluster'&&version===state.loadVersion)$('#main').innerHTML=heading('Cluster','Evidence unavailable')+empty('Could not load the cluster report',e.message,button('Try again','refresh-cluster'));}
}
function runTable(rows,selectable=false){return `<div class="table-scroll"><table class="run-table"><thead><tr>${selectable?'<th class="check-cell"><span class="muted">Pick</span></th>':''}<th>Run</th><th>Streaming</th><th>Output</th><th class="cell-num">Steady decode</th><th class="cell-num">Inclusive</th><th class="cell-num">First response</th><th>Status</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr>${selectable?`<td class="check-cell"><input type="checkbox" data-select="${esc(runId(r))}" aria-label="Select ${esc(r.arm)} for comparison" ${state.selected.has(runId(r))?'checked':''}></td>`:''}<td><button class="run-name" data-detail="${esc(runId(r))}">${esc(r.task?.label||r.arm)}</button><span class="run-sub" title="${esc(shortModel(r))}">${esc(r.engine)} <span class="footer-dot">·</span> ${esc(shortModel(r))}</span></td><td><button class="run-name" data-streaming="${esc(runId(r))}">${esc(r.streaming_method||'Not recorded')}</button><span class="run-sub">${esc(r.streaming_status==='engine'?'Engine confirmed':r.streaming_status==='requested'?'Requested':'Unknown')}</span></td><td class="num">${val(length(r),0)}<span class="run-sub">${r.tokens_inferred&&!r.tokens?'inferred tokens':'tokens'}</span></td><td class="cell-num"><strong>${val(r.tok_s_steady)}</strong><span class="run-sub">tok/s</span></td><td class="cell-num">${val(r.tok_s)}<span class="run-sub">tok/s</span></td><td class="cell-num">${val(r.first_token_s)}<span class="run-sub">seconds</span></td><td>${r.task?badge(r.task.status,r.task.status==='failed'?'red':r.task.status==='completed'?'green':'amber'):badge(r.incomplete?'Incomplete':'Complete',r.incomplete?'amber':'green')}<span class="run-sub" title="${esc(r.time_source)}">${esc(date(r))}</span></td><td><button class="icon-button" data-detail="${esc(runId(r))}" aria-label="Inspect ${esc(r.arm)}">${icon('arrow')}</button></td></tr>`).join('')||'<tr><td colspan="8"><div class="empty-state"><h3>No runs match these filters</h3><p>Try another search or clear the filters.</p></div></td></tr>'}</tbody></table></div>`;}
function recordedChart(rows){
  const data=rows.filter(r=>finite(r.tok_s_steady)).slice(0,6).reverse();if(!data.length)return empty('No decode measurements yet','Completed runs with a decode measurement will appear here.');
  const width=640,left=177,right=60,row=47,top=22,height=data.length*row+40,max=Math.max(...data.map(r=>r.tok_s_steady))*1.2;
  const inner=width-left-right;
  return `<svg class="plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="Steady decode by run, with output length shown. Runs of different lengths are not matched comparisons.">${[0,1,2,3,4].map(i=>{const x=left+inner*i/4;return `<line class="gridline" x1="${x}" x2="${x}" y1="5" y2="${height-28}"/><text x="${x}" y="${height-8}" text-anchor="middle">${fmt(max*i/4,1)}</text>`;}).join('')}${data.map((r,i)=>{const y=top+i*row,bw=r.tok_s_steady/max*inner;return `<text x="0" y="${y+2}" fill="var(--text)">${esc(r.arm.length>22?r.arm.slice(0,21)+'…':r.arm)}</text><text x="0" y="${y+17}" style="font-size:9px">${length(r)} tokens · ${esc(r.engine)}</text><rect x="${left}" y="${y-6}" width="${bw}" height="19" rx="4" fill="var(--accent)" opacity="${i===data.length-1?1:.42+i*.08}"/><text x="${left+bw+9}" y="${y+8}" style="font-weight:600">${fmt(r.tok_s_steady)}</text>`;}).join('')}</svg>`;
}
let benchmarkModule=null;
async function renderBenchmark(){
  const version=state.loadVersion;
  $('#main').innerHTML=heading('Benchmark your Mac','Measure the drives, get a hardware-aware streaming recommendation, then verify it with your model.')+'<div id=\"benchmark-content\"><p class=\"muted\">Opening benchmark…</p></div>';
  try{benchmarkModule ||= await import('/benchmark-view.js');if(state.route!=='benchmark'||version!==state.loadVersion)return;benchmarkModule.mountBenchmark($('#benchmark-content'),{api,download,token:state.settings.token,settings:state.settings,onVerify:()=>{location.hash='#monitor';setTimeout(openMonitorTest,100);}});}
  catch(e){if(state.route==='benchmark'&&version===state.loadVersion)$('#benchmark-content').innerHTML=empty('Benchmark unavailable',e.message,button('Refresh','refresh','primary','refresh'));}
}
function renderOverview(){
  const rows=state.rows,latest=rows.find(r=>!r.incomplete&&finite(r.tok_s_steady));
  const coverage=[['Complete summaries',rows.filter(r=>!r.incomplete).length,'check'],['Output fingerprints',rows.filter(r=>r.output_hash).length,'shield'],['Storage + device maps',rows.filter(r=>r.artifacts?.storage&&r.artifacts?.device_map).length,'disk'],['Memory samples',rows.filter(r=>r.artifacts?.memory).length,'memory']];
  $('#main').innerHTML=heading('Find your fastest configuration.','Compare model runs and streaming settings using measured speed, response time and output checks.',linkButton('Open monitor','monitor','','live')+linkButton('Compare runs','compare','primary','compare'))+
    `<div class="summary-strip"><span>${badge(state.data.mode==='reports'?'Saved reports':'Live collection',state.data.mode==='reports'?'purple':'green')} <span style="margin-left:8px">${rows.length} runs <span class="footer-dot">·</span> ${new Set(rows.map(r=>r.block)).size} folders</span></span><span class="source-path" title="${esc(state.settings.runs)}">${esc(state.settings.runs)}</span><a href="#settings">Change folder</a></div>`+
    (latest?`<div class="eyebrow">LATEST COMPLETE RUN <span class="footer-dot">/</span> ${esc(latest.arm)} <span class="footer-dot">/</span> ${length(latest)} TOKENS</div><div class="metrics">${metric('Steady decode',unit(latest.tok_s_steady,'tok/s'),'After the first response','bolt',true)}${metric('First response',unit(latest.first_token_s,'s'),'Includes setup and prompt processing','clock')}${metric('Inclusive throughput',unit(latest.tok_s,'tok/s'),'Generated count ÷ total response time','live')}${metric('Read volume / token',unit(latest.summary?.gen_window_gb_per_tok,'GB'),'Recorded decode window · all drives','disk')}</div>`:'')+
    (rows.length?`<div class="two-col">${panel('Recorded decode','Each bar is one run. Output lengths are shown.',`<div class="panel-body">${recordedChart(rows)}<p class="chart-note">tok/s · Use Compare to calculate gains for matching workloads.</p></div>`,badge('Last '+Math.min(rows.length,6)+' runs'))}${panel('Evidence coverage','What your saved runs can tell you.',`<div class="panel-body">${coverage.map(([label,n,sym])=>`<div class="coverage-row"><span class="coverage-label">${icon(sym)}${label}</span><span>${badge(n+' / '+rows.length,n===rows.length?'green':'')} </span></div>`).join('')}<div class="info-note" style="margin-top:16px">${icon('info')}Missing artifacts remain unavailable. They are never treated as zero measurements.</div></div>`)}</div>`:panel('Your performance workspace starts here','',empty('Connect your run folder','Open a folder containing ds4 or deltafin benchmark logs. ARGODRIVE reads them in place.',linkButton('Choose a data source','settings','primary','folder'))))+
    (rows.length?panel('Recent runs','Inspect the prompt, settings and measurement sources.',runTable(rows.slice(0,5))+`<div class="table-footer"><span>${Math.min(rows.length,5)} of ${rows.length} runs</span><a href="#runs">Open run library →</a></div>`,linkButton('All runs','runs','small','arrow')):'')+
    `<div class="two-col equal">${panel('A repeatable workflow','Make each change easier to evaluate.',`<div class="panel-body"><div class="next-action"><span class="step">01</span><div><h3>Keep a control run</h3><p>Use the same model, prompt and output length for your baseline and candidate.</p></div></div><div class="next-action"><span class="step">02</span><div><h3>Check both speed and output</h3><p>Compare steady decode, first response, configuration changes and output fingerprints together.</p></div></div><div class="next-action"><span class="step">03</span><div><h3>Repeat before promoting</h3><p>A single pair shows a result. Reversed-order repeats help establish whether the gain holds.</p></div></div></div>`)}${panel('Collection status','Know which measurements are being collected.',`<div class="panel-body"><div class="setting-line"><span>Mode</span><strong>${state.data.mode==='reports'?'Reports only':'Live monitoring'}</strong></div><div class="setting-line"><span>Hardware sampler</span><strong>${state.data.mode==='reports'?'Stopped':state.data.health?.scope?'Receiving samples':'No fresh samples'}</strong></div><div class="setting-line"><span>Data location</span><strong>Local filesystem</strong></div><div class="info-note" style="margin-top:16px">${state.data.mode==='reports'?'This workspace reads saved measurements. Opening it does not start an inference run or a drive sampler.':'Live sampling adds work to this Mac. Use reports-only mode when reviewing headline benchmark results.'}</div></div>`,linkButton('Settings','settings','small'))}</div>`;
}
let runExportScope='all';
function renderRuns(){
  const engines=[...new Set(state.rows.map(r=>r.engine))].sort(),lens=[...new Set(state.rows.map(length))].filter(finite).sort((a,b)=>a-b),f=state.filters;
  $('#main').innerHTML=heading('Run library','Find a run, inspect its evidence, or select two to compare.',`<form id="run-export-form" class="run-export"><label for="run-export-range">Export range</label><div class="run-export-controls"><select id="run-export-range" aria-describedby="run-export-note">${runExportOptions.map(([value,label])=>`<option value="${value}" ${runExportScope===value?'selected':''}>${label}</option>`).join('')}</select><button class="button" type="submit" id="run-export-button">${icon('download')}<span>${runExportScope==='all'?'Export all runs':'Export CSV'}</span></button></div><p id="run-export-note">Entire library · independent of table filters</p><p id="run-export-status" role="status" hidden></p></form>`)+
    `<div class="source-strip"><span>${state.rows.length} recorded arms · <span class="path-text">${esc(state.settings?.runs)}</span></span>${linkButton('Change folder','settings','small')}</div><div class="filters"><label class="search-input">${icon('search')}<input id="run-search" type="search" placeholder="Search run, model or prompt…" aria-label="Search runs" value="${esc(f.query||'')}"></label><select id="filter-engine" aria-label="Filter by engine"><option value="">All engines</option>${engines.map(e=>`<option ${f.engine===e?'selected':''}>${esc(e)}</option>`).join('')}</select><select id="filter-tokens" aria-label="Filter by output length"><option value="">All output lengths</option>${lens.map(n=>`<option value="${n}" ${f.tokens===String(n)?'selected':''}>${n} tokens</option>`).join('')}</select><select id="filter-status" aria-label="Filter by completion"><option value="">All statuses</option><option value="complete" ${f.status==='complete'?'selected':''}>Complete</option><option value="incomplete" ${f.status==='incomplete'?'selected':''}>Incomplete</option></select>${button('Clear','clear-filters','subtle')}</div><div id="selection"></div><section class="panel" id="run-results"></section>`;
  $('#run-search').oninput=e=>{state.filters.query=e.target.value;state.page=0;updateRunResults();};
  for(const k of ['engine','tokens','status'])$('#filter-'+k).onchange=e=>{state.filters[k]=e.target.value;state.page=0;updateRunResults();};
  $('#run-export-range').onchange=e=>{runExportScope=e.target.value;updateRunExportNote();};
  $('#run-export-form').onsubmit=exportRunLibrary;
  updateRunExportNote();
  updateRunResults();
}
function updateRunExportNote(){
  $('#run-export-button span').textContent=runExportScope==='all'?'Export all runs':'Export CSV';
  $('#run-export-note').textContent=runExportScope==='all'?'Entire library · independent of table filters':runExportScope.startsWith('hours:')?'Up to now, using recorded run time · across the whole library':'Newest first by recorded run time · across the whole library';
  if(runExportScope!=='all')$('#run-export-note').textContent+=' · undated runs excluded';
  $('#run-export-status').hidden=true;
}
async function exportRunLibrary(event){
  event.preventDefault();
  const scope=runExportScope,control=$('#run-export-button'),select=$('#run-export-range'),status=$('#run-export-status');
  control.disabled=true;select.disabled=true;status.hidden=false;status.textContent='Preparing CSV…';
  try{
    const response=await fetch(runExportURL(scope),{cache:'no-store',signal:AbortSignal.timeout(60000)});
    if(response.status===404&&scope!=='all')throw Error('Restart ARGODRIVE with the updated backend to export recent runs.');
    if(!response.ok)throw Error(`Export failed (${response.status}). Try refreshing the run library.`);
    if(!response.headers.get('Content-Type')?.includes('text/csv'))throw Error('The server did not return a CSV export.');
    const text=await response.text();
    if(text.startsWith('# no arms')){status.textContent='No runs match this range. Choose a wider range or All runs.';return;}
    const filename=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'argodrive-runs.csv';
    download(filename,text,'text/csv;charset=utf-8');
    status.textContent='CSV download started.';
  }catch(e){status.textContent=e.message;}
  finally{control.disabled=false;select.disabled=false;}
}
function updateRunResults(){
  const rows=filterRuns(state.rows,state.filters),size=25,pages=Math.max(1,Math.ceil(rows.length/size));state.page=Math.min(state.page,pages-1);
  $('#selection').innerHTML=state.selected.size?`<div class="selection-bar"><span>${state.selected.size} of 2 runs selected <span class="muted">· select the baseline first</span></span><div class="button-group">${button('Clear selection','clear-selection','subtle small')}<button class="button primary small" data-action="compare-selected" ${state.selected.size!==2?'disabled':''}>${icon('compare')}Compare selected</button></div></div>`:'';
  $('#run-results').innerHTML=runTable(rows.slice(state.page*size,(state.page+1)*size),true)+`<div class="table-footer"><span>${rows.length} matching runs</span><div class="button-group"><button class="button small" data-action="prev-page" ${state.page===0?'disabled':''}>Previous</button><span>${state.page+1} / ${pages}</span><button class="button small" data-action="next-page" ${state.page>=pages-1?'disabled':''}>Next</button></div></div>`;
}
function chooseDefaults(){
  const rows=state.rows.filter(r=>!r.incomplete);
  const a=findRun(state.baseline),b=findRun(state.candidate);
  if(a&&b)return;
  if(a){const other=rows.find(r=>compare(a,r).matched)||rows.find(r=>runId(r)!==runId(a));state.candidate=other?runId(other):'';return;}
  if(b){const other=rows.find(r=>compare(r,b).matched)||rows.find(r=>runId(r)!==runId(b));state.baseline=other?runId(other):'';return;}
  // A control-looking name is only an initial selection; visible selectors own the comparison.
  for(const r of rows){
    const others=rows.filter(x=>compare(x,r).matched);
    if(!others.length)continue;
    const control=[r,...others].find(x=>/(?:^|[-_])(off|control|ctl)(?:[-_]|$)/i.test(x.arm));
    const baseline=control||others[0],candidate=runId(baseline)===runId(r)?others[0]:r;
    state.baseline=runId(baseline);state.candidate=runId(candidate);return;
  }
  state.baseline=rows[1]?runId(rows[1]):'';state.candidate=rows[0]?runId(rows[0]):'';
}
function compareSelector(label,id,r){return `<div class="compare-select ${id==='candidate'?'candidate':''}"><label for="${id}"><span class="legend-dot" style="background:${id==='candidate'?'var(--accent)':'var(--faint)'}"></span>${label}</label><select id="${id}"><option value="">Choose a run</option>${state.rows.map(x=>`<option value="${esc(runId(x))}" ${runId(x)===state[id]?'selected':''}>${esc(x.arm)} · ${length(x)} tokens · ${esc(x.block)}</option>`).join('')}</select><p>${esc(r?shortModel(r)+' · '+date(r):'Choose from saved runs')}</p></div>`;}
function comparisonMetric(label,a,b,u,matched,lower=false){const pct=matched?gain(a,b,lower):null;return `<div class="metric ${label==='Steady decode'?'featured':''}"><div class="metric-label">${label}</div><div class="metric-value">${unit(b,u)}</div><div class="comparison-metric"><span>Baseline <strong>${val(a)} ${esc(u)}</strong></span>${pct!==null?`<span class="gain-pill ${pct<0?'negative':''}">${pct>0?'+':''}${fmt(pct,1)}% ${lower?'less time':''}</span>`:badge('No gain claim')}</div></div>`;}
function matchTable(c){return `<div class="table-scroll"><table class="tight-table"><thead><tr><th>Check</th><th>Baseline / candidate</th><th>Result</th></tr></thead><tbody>${c.checks.map(x=>`<tr><td>${esc(x.label)}</td><td class="muted" style="font-size:11px">${x.label==='Prompt'?(x.state==='match'?'Same prompt fingerprint':x.state==='different'?'Different prompt fingerprints':'Fingerprint missing'):esc(x.a??'Unknown')+' / '+esc(x.b??'Unknown')}</td><td><span class="check-state ${x.state}">${icon(x.state==='match'?'check':'info')}${x.state==='match'?'Match':x.state==='unknown'?'Unknown':'Different'}</span></td></tr>`).join('')}</tbody></table></div>`;}
function renderCompare(){
  chooseDefaults();const a=findRun(state.baseline),b=findRun(state.candidate),c=compare(a,b);
  $('#main').innerHTML=heading('Compare with confidence.','Choose an explicit baseline and candidate. Gains appear only when the recorded workload fields match.',state.rows.length?button('Export comparison','export-comparison','','download'):'')+
    (state.rows.length<2?panel('Compare runs','',empty('Two runs make a comparison','Add at least two saved runs to compare their results.',linkButton('Open settings','settings','primary'))):
    `<div class="compare-selectors">${compareSelector('Baseline','baseline',a)}<button class="icon-button" data-action="swap-comparison" aria-label="Swap baseline and candidate">${icon('compare')}</button>${compareSelector('Candidate','candidate',b)}</div>`+
    (a&&b?`<div class="comparison-banner ${c.matched?'':'unmatched'}">${icon(c.matched?'check':'info')}<div><h3>${esc(c.reason)}</h3><p>${c.matched?'One pair, without a confidence interval. Repeat in reversed order before promoting a change.':'The measurements are shown side by side. Different or missing workload fields prevent a supported gain calculation.'}</p></div></div><div class="metrics">${comparisonMetric('Steady decode',a.tok_s_steady,b.tok_s_steady,'tok/s',c.matched)}${comparisonMetric('First response',a.first_token_s,b.first_token_s,'s',c.matched,true)}${comparisonMetric('Inclusive throughput',a.tok_s,b.tok_s,'tok/s',c.matched)}${comparisonMetric('Read volume / token',a.summary?.gen_window_gb_per_tok,b.summary?.gen_window_gb_per_tok,'GB',c.matched,true)}</div><div class="two-col equal">${panel('Configuration changes','Requested settings and recorded engine configuration.',`<div id="config-diff"><div class="panel-body"><p class="muted">Loading saved settings…</p></div></div>`)}${panel('Workload checks','These checks use the run logs, not their filenames.',matchTable(c))}</div><div class="two-col equal">${panel('Output consistency','A byte fingerprint checks identity, not answer quality.',`<div class="panel-body"><div class="coverage-row"><span>${c.output==='same'?'Output fingerprints match':c.output==='different'?'Output fingerprints differ':'Output fingerprints unavailable'}</span>${badge(c.output==='same'?'Identical hash':c.output==='different'?'Inspect output':'Not recorded',c.output==='same'?'green':'amber')}</div><p class="info-note" style="margin-top:12px">${c.output==='same'?'Both saved MD5 fingerprints are equal. This does not establish correctness, reasoning quality or coverage of other prompts.':'Review the generated text before promoting. A speed result alone does not establish output quality.'}</p></div>`)}${panel('Measurement context','Keep the claim as narrow as the evidence.',`<div class="panel-body"><div class="setting-line"><span>Baseline output length</span><strong>${length(a)} tokens</strong></div><div class="setting-line"><span>Candidate output length</span><strong>${length(b)} tokens</strong></div><div class="info-note" style="margin-top:15px">First response includes prompt processing and setup. For ds4 harness logs it is time to first response byte; generated counts use response chunks. Prefill throughput must be measured separately.</div></div>`)}</div>`:'') );
  if($('#baseline')){for(const id of ['baseline','candidate'])$('#'+id).onchange=e=>{state[id]=e.target.value;renderCompare();};if(a&&b)loadConfigDiff(a,b);}
}
async function detailFor(r){const id=runId(r);if(!state.detail.has(id)){const d=await api(`/arm?block=${encodeURIComponent(r.block)}&arm=${encodeURIComponent(r.arm)}`);state.detail.set(id,d);}return state.detail.get(id);}
function flatDetail(d){const result={};for(const [section,obj]of [['Config',d.config],['Environment',d.environment],['Engine evidence',d.settings]])for(const[k,v]of Object.entries(obj||{}))result[section+' / '+k]=v;if(d.engine_build)result['Build / identity']=d.engine_build;return result;}
async function loadConfigDiff(a,b){const selected=state.baseline+'|'+state.candidate;try{
  const [da,db]=await Promise.all([detailFor(a),detailFor(b)]);if(state.route!=='compare'||selected!==state.baseline+'|'+state.candidate)return;
  const diffs=settingsDiff(flatDetail(da),flatDetail(db));$('#config-diff').innerHTML=diffs.length?`<div class="table-scroll"><table class="config-table"><thead><tr><th>Setting</th><th>Baseline</th><th>Candidate</th></tr></thead><tbody>${diffs.map(d=>`<tr><td>${esc(d.key)}</td><td>${esc(d.a??'Not recorded')}</td><td class="text-accent">${esc(d.b??'Not recorded')}</td></tr>`).join('')}</tbody></table></div><div class="panel-body" style="padding-top:16px"><div class="info-note">${diffs.length} recorded difference${diffs.length===1?'':'s'}. ${diffs.length>1?'This pair cannot attribute a gain to one setting in isolation.':'A requested setting still needs evidence that the engine applied it.'}</div></div>`:`<div class="panel-body"><p class="info-note">No differences in the recorded configuration. Unrecorded defaults and machine state may still differ.</p></div>`;
  }catch(e){if($('#config-diff'))$('#config-diff').innerHTML=`<div class="panel-body"><p class="data-error">${esc(e.message)}</p></div>`;}
}
async function showDetail(id){const r=findRun(id);if(!r)return;const dialog=$('#run-dialog');$('#detail-content').innerHTML=`<div class="detail-head"><h2 id="detail-title">${esc(r.arm)}</h2><button class="icon-button" data-action="close-detail" aria-label="Close run details">${icon('close')}</button></div><div class="detail-body"><p class="muted">Reading run details…</p></div>`;if(!dialog.open)dialog.showModal();dialog.dataset.run=id;
  try{const d=await detailFor(r);if(dialog.dataset.run!==id||!dialog.open)return;$('#detail-content').innerHTML=`<div class="detail-head"><div><div class="eyebrow">${esc(r.engine)} · ${length(r)} TOKENS</div><h2 id="detail-title">${esc(r.arm)}</h2><p class="muted" style="font-size:12px;margin-top:7px">${esc(shortModel(r))}</p></div><button class="icon-button" data-action="close-detail" aria-label="Close run details">${icon('close')}</button></div><div class="detail-body"><div class="metrics">${metric('Steady decode',unit(r.tok_s_steady,'tok/s'),'After first response')}${metric('Inclusive',unit(r.tok_s,'tok/s'),'Includes first response','live')}${metric('First response',unit(r.first_token_s,'s'),'Harness measurement','clock')}</div><div class="button-group"><button class="button primary" data-baseline="${esc(id)}">${icon('compare')}Use as baseline</button><button class="button" data-candidate="${esc(id)}">Use as candidate</button></div><h3>Streaming method</h3><div class="info-note">${esc(d.streaming?.method||'Not recorded')} · ${esc(d.streaming?.status||'unknown')}</div><button class="button small" data-streaming="${esc(id)}">Inspect streaming settings & files</button><h3>Prompt</h3><div class="prompt-box">${esc(d.prompt||'Prompt not recorded in this log.')}</div><h3>Available evidence</h3><div class="button-group">${Object.entries(r.artifacts||{}).map(([k,v])=>badge(k.replaceAll('_',' ')+(v?' ✓':' —'),v?'green':'')).join('')}</div><h3>Measurement sources</h3><div class="info-note">${esc(r.rate_source||'Engine cumulative statistics; steady decode derived after the first token.')}<br>Time shown: ${esc(r.time_source)}. Storage averages, when present, may include loading and idle time.</div>${r.summary?`<h3>Recorded decode diagnostics</h3><div class="setting-line"><span>Read volume / token</span><strong>${fmt(r.summary.gen_window_gb_per_tok)} GB</strong></div><div class="setting-line"><span>Cache hit rate · reported lookups</span><strong>${fmt(r.cache_hit_pct,1)}%</strong></div><p class="chart-note">The harness hit rate may include prefetch activity. It is not necessarily demand-read hit rate.</p><div class="setting-line"><span>Swap growth during run</span><strong>${fmt(r.summary.swap_growth_mb,0)} MB</strong></div>`:''}<details><summary>Configuration and engine evidence</summary><pre>${esc(JSON.stringify({config:d.config,environment:d.environment,engine_build:d.engine_build,engine_evidence:d.settings,native:d.native},null,2))}</pre></details><details><summary>Recorded counters</summary><pre>${esc(JSON.stringify(d.summary||d.counters,null,2))}</pre></details></div>`;
  }catch(e){$('.detail-body').innerHTML=`<p class="data-error">${esc(e.message)}</p>`;}
}
let monitorTestRun={status:'idle',owned:false}, monitorTestOptions=null, monitorTestBusy=false;
function monitorTestButtons(){return `<button class="button" id="monitor-test-toggle">Test setup</button><button class="button danger" id="monitor-test-stop" ${monitorTestRun.owned?'':'hidden'}>${monitorTestRun.status==='stopping'?'Stopping…':'Stop test'}</button>`;}
async function pollMonitorTest(){
  if(state.route!=='monitor'||document.hidden||monitorTestBusy)return;
  try{const d=await api('/test-runner');monitorTestRun=d.run;const stop=$('#monitor-test-stop');if(stop){stop.hidden=!d.run.owned;stop.disabled=d.run.status==='stopping';stop.textContent=stop.disabled?'Stopping…':'Stop test';}const status=$('#monitor-test-status');if(status)status.textContent=testStatusText();const submit=$('#monitor-test-start');if(submit)submit.disabled=!!d.run.owned||!monitorTestOptions?.models.some(m=>m.id===$('#monitor-test-model')?.value&&m.ready);}
  catch(e){/* Older preview backends can still display measurements. Setup reports the error. */}
}
function testStatusText(){const r=monitorTestRun;return r.status==='idle'?'Ready for one test.':`${r.status}${r.model?' · '+r.model:''}${r.decode_tok_s?' · '+fmt(r.decode_tok_s)+' tok/s':''}${r.error?' · '+r.error:''}${r.errors?.length?' · '+r.errors.join(' '):''}`;}
async function openMonitorTest(){
  let dialog=$('#monitor-test-dialog');if(!dialog){dialog=document.createElement('dialog');dialog.id='monitor-test-dialog';dialog.className='monitor-test-dialog';document.body.append(dialog);}
  dialog.innerHTML='<div class="test-dialog-head"><h2>Run a test</h2><button class="icon-button" id="monitor-test-close" aria-label="Close test setup">×</button></div><div id="monitor-test-body">Checking launch profiles…</div>';
  $('#monitor-test-close').onclick=()=>dialog.close();if(!dialog.open)dialog.showModal();
  try{
    const d=await api('/test-runner?options=1');monitorTestOptions=d.options;monitorTestRun=d.run;
    const defaultModel=d.options.default_model;
    $('#monitor-test-body').innerHTML=`<form id="monitor-test-form"><label class="field">Model / engine<select id="monitor-test-model">${d.options.models.map(m=>`<option value="${esc(m.id)}" ${m.id===defaultModel?'selected ':''}${m.ready?'':'disabled'}>${esc(m.label)}${m.ready?' · '+esc(m.engine):' — '+esc(m.reason)}</option>`).join('')}</select></label><fieldset><legend>Read weights from these drives</legend><div id="monitor-test-drives"></div></fieldset><div class="test-size-fields"><label class="field">Context capacity<select id="monitor-test-context">${d.options.contexts.map(n=>`<option value="${n}" ${n===4096?'selected':''}>${n.toLocaleString()} tokens</option>`).join('')}</select><small>Maximum context capacity; the prompt stays fixed.</small></label><label class="field">Generate<select id="monitor-test-tokens">${d.options.tokens.map(n=>`<option value="${n}" ${n===100?'selected':''}>${n} tokens</option>`).join('')}</select><small>Number of output tokens for this test.</small></label></div><p class="test-settings-note" id="monitor-test-settings"></p><p class="test-settings-note">One cold application-cache run, greedy output, no speculation. Selected drives use existing full replicas; no files are copied. Results and exact settings are saved in the Run library. Larger context uses the engine’s automatic cache budget.</p><div class="test-dialog-actions"><span id="monitor-test-status" role="status">${esc(testStatusText())}</span><button class="button primary" id="monitor-test-start" type="submit" ${d.run.owned?'disabled':''}>Start test</button></div></form>`;
    const update=()=>{const model=d.options.models.find(m=>m.id===$('#monitor-test-model').value);$('#monitor-test-drives').innerHTML=(model?.drives||[]).map(x=>`<label class="test-drive-option"><input type="checkbox" value="${esc(x.id)}" ${x.ready?'checked':'disabled'}><span>${esc(x.label)}<small>${x.ready?'Split weight '+x.weight:esc(x.reason)}</small></span></label>`).join('')||'<p>No runnable drive profile is configured.</p>';$('#monitor-test-start').disabled=!!monitorTestRun.owned||!model?.ready;$('#monitor-test-settings').textContent=(model?.read_method||'ds4 split reads')+' · '+(model?.read_threads||48)+' read workers · read-ahead '+(model?.read_ahead||'configured')+' · F_NOCACHE · cache: '+(Number($('#monitor-test-context').value)<=4096?'70 GB':'automatic');};
    $('#monitor-test-model').onchange=update;$('#monitor-test-context').onchange=update;update();
    $('#monitor-test-form').onsubmit=async e=>{e.preventDefault();if(monitorTestBusy)return;monitorTestBusy=true;$('#monitor-test-start').disabled=true;$('#monitor-test-status').textContent='Checking selected drives and engine…';try{monitorTestRun=await api('/test-runner/start',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:JSON.stringify({model:$('#monitor-test-model').value,drives:[...dialog.querySelectorAll('#monitor-test-drives input:checked')].map(x=>x.value),context:Number($('#monitor-test-context').value),tokens:Number($('#monitor-test-tokens').value)})});state.monitorSource='latest';state.monitorSourceSet=true;state.monitorCursor=null;state.monitorEpoch++;harnessSelected='';dialog.close();renderMonitor();toast('Test started. Monitor will follow its recording.');}catch(err){$('#monitor-test-status').textContent=err.message;$('#monitor-test-start').disabled=false;}finally{monitorTestBusy=false;}};
  }catch(e){$('#monitor-test-body').textContent='Test controls unavailable: '+e.message;}
}
async function stopMonitorTest(){if(!monitorTestRun.owned)return;try{monitorTestRun=await api('/test-runner/stop',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:JSON.stringify({id:monitorTestRun.id})});toast('Stopping this Monitor test; partial results will be retained.');pollMonitorTest();}catch(e){toast(e.message);}}
document.addEventListener('click',e=>{if(e.target.closest('#monitor-test-toggle'))openMonitorTest();if(e.target.closest('#monitor-test-stop'))stopMonitorTest();});
setInterval(pollMonitorTest,2000);

let harnessSelected='',harnessBusy=false,harnessData=null;
function monitorStages(d){
  const stage=d.stage||{}, timings=stage.timings||{}, current=stage.current||'unknown';
  const seen=new Set(stage.seen||[]), labels={setup:'Load / setup',prefill:'Prefill / encode',decode:'Decode',finalizing:'Saving results',complete:'Complete',failed:'Failed',unknown:'Waiting for engine stage'};
  const terminal=['complete','failed'].includes(current), live=stage.live&&state.monitorSource==='latest';
  const steps=[['setup','Load / setup'],['prefill','Prefill / encode'],['decode','Decode']];
  const progress=stage.progress;
  const detail=timings.prefill_tok_s!==undefined?`Prefill ${fmt(timings.prefill_tok_s,2)} tok/s · ${timings.prompt_tokens} input tokens · Generation ${fmt(timings.generation_tok_s,2)} tok/s · ${timings.generated_tokens} generated tokens`:timings.prefill_seconds!==undefined?`Prefill ${fmt(timings.prefill_seconds,2)} s · ${timings.prompt_tokens} input tokens · Decode ${fmt(timings.decode_seconds,2)} s · ${timings.generated_tokens} generated tokens`:
    current==='prefill'&&progress?`${progress.processed} / ${progress.total} input tokens${progress.processed===progress.total?' · waiting for first response':''}`:
    current==='decode'?`${d.chunks||0} response chunks · token count confirmed at completion`:
    'Waiting for explicit prompt progress; short prefills may report only at completion.';
  return `<section class="monitor-stages" aria-label="Inference stages"><div class="monitor-stage-steps">${steps.map(([id,label],i)=>`<div class="monitor-stage ${live&&current===id?'active':seen.has(id)?'observed':''}" ${live&&current===id?'aria-current="step"':''}><span>${i+1}</span>${label}</div>`).join('')}<strong class="monitor-stage-state">${esc(!live&&!terminal?'Last reported: ':'')}${esc(labels[current]||labels.unknown)}</strong></div><p>${esc(detail)}${state.monitorSource==='past'?' · Whole-run stages; not the chart cursor.':''}</p></section>`;
}
function isTestMonitor(){return state.route==='monitor'&&state.monitorSource!=='hardware';}

/* Advanced read analysis.
 *
 * The throughput charts answer "how many bytes per second", and on this workload
 * they never approach the drives' combined ceiling — yet adding a drive still
 * makes decode faster. That is because a streamed MoE does not wait for
 * bandwidth, it waits for one read to finish. Splitting a read across N drives
 * leaves each drive a smaller slice, so the slice that finishes last finishes
 * sooner, even though the peak rate barely moves.
 *
 * These panels show the quantity that actually moves: milliseconds per read,
 * and how many reads are in flight while the engine waits. Both come from
 * IOKit's completed-read statistics (bytes, count and total service time per
 * device), which the backend already reports as read_operations.
 */

const PAD = {l: 46, r: 12, t: 12, b: 20};
const W = 640, H = 150;

const concurrency = s => finite(s?.read_iops) && finite(s?.mean_read_ms) ? s.read_iops * s.mean_read_ms / 1000 : null;

function axis(max, unit, digits) {
  const rows = [0, .25, .5, .75, 1].map(f => {
    const y = PAD.t + (H - PAD.t - PAD.b) * (1 - f), v = max * f;
    return `<line class="adv-grid" x1="${PAD.l}" y1="${y.toFixed(1)}" x2="${W - PAD.r}" y2="${y.toFixed(1)}"></line>` +
           `<text class="adv-tick" x="${PAD.l - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end">${fmt(v, digits)}</text>`;
  }).join('');
  return rows + `<text class="adv-unit" x="${PAD.l - 6}" y="${PAD.t - 3}" text-anchor="end">${esc(unit)}</text>`;
}

/* One line per drive over the client-side history buffer. Points are spaced by
 * their own timestamps, so a stalled poll leaves a gap rather than a fake slope. */
function series(history, devices, pick, max, unit, digits) {
  if (!history.length || !(max > 0)) return '';
  const t0 = history[0].t, t1 = history[history.length - 1].t, span = Math.max(1, t1 - t0);
  const x = t => PAD.l + (W - PAD.l - PAD.r) * ((t - t0) / span);
  const y = v => PAD.t + (H - PAD.t - PAD.b) * (1 - Math.min(1, v / max));
  let peak = {v: -1, t: 0, id: ''};
  const lines = devices.map(dev => {
    const pts = [];
    for (const row of history) {
      const v = pick(row.ops?.[dev.id]);
      if (!finite(v)) { pts.push(null); continue; }
      if (v > peak.v) peak = {v, t: row.t, id: dev.id};
      pts.push(`${x(row.t).toFixed(1)},${y(v).toFixed(1)}`);
    }
    const runs = [];
    let run = [];
    for (const p of pts) { if (p) run.push(p); else if (run.length) { runs.push(run); run = []; } }
    if (run.length) runs.push(run);
    return runs.filter(r => r.length > 1)
      .map(r => `<polyline class="adv-line" points="${r.join(' ')}" style="stroke:${esc(dev.color || '#6153dc')}"></polyline>`).join('');
  }).join('');
  /* Mark the worst moment: this is the "when does it spike" question. */
  const mark = peak.v > 0 ? `<circle class="adv-peak-dot" cx="${x(peak.t).toFixed(1)}" cy="${y(peak.v).toFixed(1)}" r="3.5"></circle>` +
    `<text class="adv-peak-label" x="${Math.min(W - PAD.r - 4, x(peak.t) + 6).toFixed(1)}" y="${Math.max(PAD.t + 10, y(peak.v) - 6).toFixed(1)}">peak ${fmt(peak.v, digits)} ${esc(unit)}</text>` : '';
  return `<svg class="adv-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(unit)} per drive over the sampled window; peak ${fmt(peak.v, digits)}">${axis(max, unit, digits)}${lines}${mark}</svg>`;
}

function bar(value, max, color) {
  const pct = finite(value) && max > 0 ? Math.max(1, Math.min(100, value / max * 100)) : 0;
  return `<span class="adv-bar"><i style="width:${pct.toFixed(1)}%;background:${esc(color)}"></i></span>`;
}

function advancedReadsView(data, history, {window: seconds = 20, devices = []} = {}) {
  const ops = data.read_operations || {};
  const live = devices.filter(d => d.present !== false);
  if (!live.length) {
    return `<section class="panel"><div class="panel-head"><div><h2>Advanced read analysis</h2>
      <p>No drives are reporting completed reads yet.</p></div></div></section>`;
  }
  const end = data.seconds;
  const rows = live.map(dev => {
    const s = ops[dev.id] || {};
    const win = readWindowStats(data.read_windows?.[dev.id] || [], seconds, end);
    return {dev, s, win, flight: concurrency(s)};
  });
  const maxMs = Math.max(0.05, ...history.flatMap(r => live.map(d => r.ops?.[d.id]?.mean_read_ms || 0)), ...rows.map(r => r.s.mean_read_ms || 0));
  const maxFlight = Math.max(0.5, ...history.flatMap(r => live.map(d => concurrency(r.ops?.[d.id]) || 0)), ...rows.map(r => r.flight || 0));
  const maxPeak = Math.max(0.1, ...rows.map(r => r.win.peak || 0));
  const totalFlight = rows.reduce((a, r) => a + (r.flight || 0), 0);
  const slowest = rows.reduce((a, r) => (r.s.mean_read_ms || 0) > (a?.s.mean_read_ms || 0) ? r : a, null);

  const legend = rows.map(r => `<span class="adv-key"><i style="background:${esc(r.dev.color || '#6153dc')}"></i>${esc(r.dev.label || r.dev.id)}</span>`).join('');
  const table = rows.map(r => `<tr>
      <th scope="row"><span class="adv-swatch" style="background:${esc(r.dev.color || '#6153dc')}"></span>${esc(r.dev.label || r.dev.id)}</th>
      <td class="cell-num">${fmt(r.s.mean_read_ms, 2)}<span class="run-sub">ms / read</span></td>
      <td>${bar(r.s.mean_read_ms, maxMs, r.dev.color || '#6153dc')}</td>
      <td class="cell-num">${fmt(r.flight, 2)}<span class="run-sub">in flight</span></td>
      <td class="cell-num">${fmt(r.win.peak, 2)}<span class="run-sub">peak GB/s</span></td>
      <td class="cell-num">${fmt(r.win.mean, 2)}<span class="run-sub">mean GB/s</span></td>
      <td class="cell-num">${finite(r.win.peak) && r.win.peak > 0 ? fmt(r.win.mean / r.win.peak * 100, 0) : '—'}<span class="run-sub">% duty</span></td>
      <td class="cell-num">${fmt(r.s.mean_read_kib, 0)}<span class="run-sub">KiB / read</span></td>
    </tr>`).join('');

  return `<section class="panel adv-panel"><div class="panel-head"><div><h2>Advanced read analysis</h2>
      <p>Milliseconds per read and reads in flight, from IOKit completed-read statistics. This is the quantity an extra drive changes.</p></div>
      <div class="adv-legend">${legend}</div></div>
    <div class="panel-body adv-body">
      <div class="adv-explain">A streamed MoE waits for <strong>one read to finish</strong>, not for bandwidth. Splitting each read across more drives gives every drive a smaller slice, so the slice that lands last lands sooner &mdash; the peak rate barely moves while the wait shrinks. Watch <strong>ms / read</strong> fall as drives are added, and compare <strong>% duty</strong>: a low duty cycle with a high peak means the drives are idle between bursts, so bandwidth is not the limit.</div>
      <div class="adv-grid-2">
        <figure><figcaption>Time per read &middot; ms &middot; last ${history.length} samples</figcaption>${series(history, live, s => s?.mean_read_ms, maxMs, 'ms', 2) || '<p class="muted">Collecting&hellip;</p>'}</figure>
        <figure><figcaption>Reads in flight &middot; service time &divide; elapsed</figcaption>${series(history, live, concurrency, maxFlight, 'in flight', 2) || '<p class="muted">Collecting&hellip;</p>'}</figure>
      </div>
      <div class="table-scroll"><table class="adv-table"><caption>Per drive over the last ${seconds} seconds &middot; peak is the highest sampled interval, duty is mean &divide; peak</caption>
        <thead><tr><th>Drive</th><th class="cell-num">Latency</th><th></th><th class="cell-num">Concurrency</th><th class="cell-num">Peak</th><th class="cell-num">Mean</th><th class="cell-num">Duty</th><th class="cell-num">Size</th></tr></thead>
        <tbody>${table}</tbody></table></div>
      <div class="adv-readout">
        <span>Combined reads in flight <strong>${fmt(totalFlight, 2)}</strong></span>
        <span>Slowest drive now <strong>${slowest ? esc(slowest.dev.label || slowest.dev.id) : '—'}</strong>${slowest && finite(slowest.s.mean_read_ms) ? ` at <strong>${fmt(slowest.s.mean_read_ms, 2)} ms</strong>` : ''}</span>
        <span class="muted">A split read completes when the slowest slice does, so that drive sets the wait.</span>
      </div>
    </div></section>`;
}

/* read_operations is a rolling 5 s snapshot, so the advanced charts keep their own
 * short history here. Timestamped, so a missed poll shows a gap, not a slope. */
const readOpsHistory=[];
function captureReadOps(d){
  if(!d||!d.read_operations)return;
  const t=Date.now()/1000,last=readOpsHistory[readOpsHistory.length-1];
  if(last&&t-last.t<0.5)return;
  readOpsHistory.push({t,ops:d.read_operations});
  while(readOpsHistory.length>240)readOpsHistory.shift();
}
function monitorHeading(){
  const hardware=state.monitorSource==='hardware';
  return heading('Monitor','Choose current hardware activity or a test’s recorded measurements.',
    `<fieldset class="monitor-mode-toggles"><legend>Chart layers</legend><label><input type="checkbox" id="monitor-show-ssd" ${state.monitorShowSSD?'checked':''}> SSDs</label><label><input type="checkbox" id="monitor-show-engram" ${state.monitorShowEngram?'checked':''}> Monitor Engram</label><label title="Milliseconds per read and reads in flight — what an extra drive actually changes"><input type="checkbox" id="monitor-advanced" ${state.monitorAdvanced?'checked':''}> Advanced</label></fieldset><label class="live-toolbar">Source <select id="monitor-source" aria-label="Monitor data source">${[['latest','Follow latest test'],['hardware','Live hardware'],['past','Past test…']].map(([v,label])=>`<option value="${v}" ${state.monitorSource===v?'selected':''}>${label}</option>`).join('')}</select></label><label class="live-toolbar">Window <select id="live-window" aria-label="Drive chart time window">${[[10,'Past 10 seconds'],[20,'Past 20 seconds'],[30,'Past 30 seconds'],[60,'Past minute'],[300,'Past 5 minutes']].map(([n,label])=>`<option value="${n}" ${state.liveWindow===n?'selected':''}>${label}</option>`).join('')}</select></label>`+
    (hardware?button(state.paused?'Resume display':'Pause display','pause-live','',state.paused?'play':'pause'):linkButton('Run library','runs','','runs'))+monitorTestButtons())+
    `<div class="monitor-source-note" title="${hardware?'Includes other apps and background work.':'Switching sources does not start a test or change hardware collection.'}"><span>${hardware?'Live · system-wide reads':'Runner-recorded SSD reads'} · GB/s · fixed 0–16 GB/s per drive · solid average / dashed peak${state.monitorShowEngram?' · <i class="monitor-key weights"></i> weights / <i class="monitor-key engram"></i> Engram':''}</span><label class="monitor-combined-toggle"><input type="checkbox" id="monitor-combined" ${state.monitorCombined!==false?'checked':''}> Show combined</label></div>`;
}
function bindMonitorControls(){
  if($('#monitor-advanced'))$('#monitor-advanced').onchange=e=>{state.monitorAdvanced=e.target.checked;safeStore('argodrive-monitor-advanced',String(state.monitorAdvanced));renderMonitor();};
  if($('#monitor-show-ssd'))$('#monitor-show-ssd').onchange=e=>{state.monitorShowSSD=e.target.checked;safeStore('argodrive-monitor-show-ssd',String(state.monitorShowSSD));renderMonitor();};
  if($('#monitor-show-engram'))$('#monitor-show-engram').onchange=e=>{state.monitorShowEngram=e.target.checked;safeStore('argodrive-monitor-show-engram',String(state.monitorShowEngram));renderMonitor();};
  if($('#monitor-combined'))$('#monitor-combined').onchange=e=>{state.monitorCombined=e.target.checked;safeStore('argodrive-monitor-combined',String(state.monitorCombined));renderMonitor();$('#monitor-combined')?.focus();};
  if($('#monitor-source'))$('#monitor-source').onchange=e=>{
    if(!['hardware','latest','past'].includes(e.target.value))return;
    state.monitorSource=e.target.value;state.monitorSourceSet=true;state.monitorCursor=null;state.monitorEpoch++;
    if(state.monitorSource==='latest')harnessSelected='';
    else if(state.monitorSource==='past'&&!harnessSelected)harnessSelected=harnessData?.id||'';
    renderMonitor();
  };
  if($('#live-window'))$('#live-window').onchange=e=>{
    const seconds=Number(e.target.value);if(![10,20,30,60,300].includes(seconds))return;
    state.liveWindow=seconds;
    if(state.monitorSource==='hardware')renderLive();else if(harnessData)updateHarness(harnessData);
  };
}
function renderMonitor(){state.monitorSource==='hardware'?renderLive():renderRunMonitor();}
function renderRunMonitor(){
  $('#main').innerHTML=monitorHeading()+
    `<div class="ssd-toolbar monitor-test-toolbar" ${state.monitorSource==='past'?'':'hidden'}><label ${state.monitorSource==='past'?'':'hidden'}>Test <select id="harness-arm" aria-label="Recorded test"><option value="">Loading tests…</option></select></label><span>${badge(state.monitorSource==='past'?'Selected recording':'Following latest test','purple')}</span></div><div id="harness-content"></div>`;
  bindMonitorControls();
  $('#harness-arm').onchange=e=>{harnessSelected=e.target.value;harnessData=null;state.monitorCursor=null;state.monitorEpoch++;$('#harness-content').innerHTML=empty('Loading test','Reading the selected measurement files.');loadHarness();};
  if(harnessData&&(!harnessSelected||harnessData.id===harnessSelected))updateHarness(harnessData);
  loadHarness();
}
async function loadHarness(){
  if(harnessBusy||!isTestMonitor())return;
  harnessBusy=true;const selected=harnessSelected,epoch=state.monitorEpoch,source=state.monitorSource;
  try{
    const data=await api('/harness'+(selected?'?arm='+encodeURIComponent(selected):''));
    if(!isTestMonitor()||selected!==harnessSelected||epoch!==state.monitorEpoch||source!==state.monitorSource)return;
    if(source==='past'&&!harnessSelected&&data.id)harnessSelected=data.id;
    harnessData=data;updateHarness(data);
  }catch(e){if(isTestMonitor()&&epoch===state.monitorEpoch)$('#harness-content').innerHTML=empty('Run monitor unavailable',e.message,linkButton('Check source folder','settings','primary'));}
  finally{harnessBusy=false;}
}
function updateHarness(d){
  if(!$('#harness-content'))return;
  const select=$('#harness-arm');
  if(select&&document.activeElement!==select)select.innerHTML=`<option value="" disabled ${!harnessSelected?'selected':''}>Choose a recorded test</option>`+(d.choices||[]).map(r=>`<option value="${esc(r.id)}" ${r.id===harnessSelected?'selected':''}>${esc(r.label)}</option>`).join('');
  if(!d.arm){$('#harness-content').innerHTML=empty('Waiting for a harness run','Choose the parent folder containing harness runs or Argodrive ds4-bench results. SSD charts require recorded sampler files.',linkButton('Choose run folder','settings','primary'));return;}
  const latest=d.seconds||0,available=(Object.values(d.read_windows||{}).flat()).filter(p=>finite(p[0])&&finite(p[2]));
  const earliest=available.length?Math.max(0,Math.min(...available.map(p=>p[0]-p[2]))):0;
  const end=state.monitorSource==='past'&&finite(state.monitorCursor)?Math.max(earliest,Math.min(latest,state.monitorCursor)):latest;
  const tot=readWindowStats(d.read_windows?.TOTAL||[],state.liveWindow,end),summary=d.summary||{},mem=d.memory||{},remote=d.remote||{};
  const drives=(d.devices||[]).map((x,i)=>({...x,color:devColor(x.id,i),stats:readWindowStats(d.read_windows?.[x.id]||[],state.liveWindow,end)}));
  const barScale=monitorReadBarScale(drives,d.read_windows,state.liveWindow,end);
  const age=finite(d.sample_age_s)?(d.sample_age_s<60?fmt(d.sample_age_s,0)+' s':fmt(d.sample_age_s/60,0)+' min'):'unknown';
  const remoteCount=(remote.cache_hits||0)+(remote.cache_misses||0);
  // Compatibility with preview backends whose legacy read-labelled field held iostat total I/O.
  const deviceIO=remote.device_io_mb??remote.physical_disk_read_mb;
  const links=Object.entries(remote.interface_tx_bytes||{}),maxBytes=Math.max(1,...links.map(([,v])=>v));
  const diskCards=(state.monitorShowSSD||state.monitorShowEngram)?drives.map(x=>`<section class="device-card live-drive-card"><div class="live-drive-head"><div><h3><span class="legend-dot" style="background:${x.color}"></span>${esc(x.label?.toLowerCase()==='internal'?'Internal':x.label)}</h3><p>${esc(x.device)}</p></div>${d.fresh&&state.monitorSource==='latest'?`<div class="metric-value" title="Latest recorded read rate">${unit(x.stats.points.length?x.stats.points.at(-1)[1]:null,'GB/s')}</div>`:''}</div>${driveReadBars(d.read_windows?.[x.id],x.color,barScale,state.liveWindow,d,x.id)}</section>`).join(''):'';
  const timeline=state.monitorSource==='past'&&available.length?`<div class="monitor-timeline"><label for="monitor-cursor">Recorded position <strong id="monitor-position">${fmt(end,1)}s</strong></label><input id="monitor-cursor" aria-label="Recorded timeline position" type="range" min="${earliest}" max="${latest}" step="0.1" value="${end}"><button class="button small" id="monitor-latest">Latest available</button><p>Available recording: ${fmt(earliest,1)}–${fmt(latest,1)}s · last up to 120 seconds retained by the reader. Window averages describe this position; engine totals and memory below describe the saved run.</p></div>`:'';
  $('#harness-content').innerHTML=`<div class="summary-strip"><span>${badge(d.state,d.fresh?'green':d.done?'purple':'amber')} <strong style="margin-left:10px">${d.model_path?esc(d.model_path.split('/').at(-1).replace(/\.gguf$/,''))+' · ':''}${esc(d.arm)}</strong></span><span>${d.kind==='task'?'Task activity · no token-speed measurement':`${d.requested_tokens||'—'} requested tokens · ${d.kind==='ds4-bench'?(d.generated_tokens??'—')+' generated tokens':(d.chunks||0)+' response chunks'}`}</span></div><p class="chart-note monitor-run-note">${esc(d.block)} · ${d.done?'Recorded window retained; this task or arm is finished.':d.catching_up?'Reading the existing history…':'File activity '+age+' ago. Engine phase is not inferred from SSD activity.'}</p>`+
    (d.kind==='task'?`<section class="panel" style="padding:18px"><strong>${esc(d.task.phase||d.task.kind)}</strong><p>${esc(d.task.detail||d.task.note||'Task in progress.')}</p><p class="chart-note">${esc(d.task.sampling||'No sampler attached')}</p></section>`:monitorStages(d))+timeline+(d.kind==='task'?`<div class="metrics">${metric('Host SSD average',unit(tot.mean,'GB/s'),'Visible recorded window','disk')}${metric('Host SSD peak',unit(tot.peak,'GB/s'),'Common device intervals','live')}</div>`:d.kind==='ds4-bench'?`<div class="metrics">${metric('Generation',unit(summary.ds4_gen_tps,'tok/s'),'All generated tokens · engine timer','bolt',true)}${metric('Steady decode',unit(summary.ds4_steady_tps,'tok/s'),'Excludes first decode step','bolt')}${metric('Prompt processing',unit(summary.ds4_prefill_tps,'tok/s'),'Engine benchmark','clock')}${metric('First decode step',unit(summary.first_decode_step_ms,'ms'),'Not time to first response','clock')}</div>`:`<div class="metrics">${metric(d.done?'Engine decode':'Response rate',unit(d.done?summary.ds4_gen_tps:d.response_rate,d.done?'tok/s':'chunks/s'),d.done?'Engine’s final timer':'Last 10 s · stdout writes, not tokenizer tokens','bolt',true)}${metric('Host SSD average',unit(tot.mean,'GB/s'),'Visible window · sampled idle included','disk')}${metric('Host SSD peak',unit(tot.peak,'GB/s'),'Common intervals · visible window','live')}${metric('First response',unit(summary.first_byte_s,'s'),'Final harness timing, when available','clock')}</div>`)+
    (drives.length&&(state.monitorShowSSD||state.monitorShowEngram)?`${state.monitorShowEngram&&!hasAttribution(d,drives)?`<div class="monitor-attribution-note"><strong>Weights + Engram overlay enabled</strong><span>${state.monitorShowSSD?'Waiting for per-request class telemetry. Grey bars remain device-wide until the engine supplies class intervals.':'Waiting for per-request class telemetry; SSD bars are hidden until selected.'}</span></div>`:''}<div class="live-drive-stack">${diskCards}${state.monitorShowSSD&&state.monitorCombined!==false?combinedReadCard(d.read_windows?.TOTAL,barScale,state.liveWindow,drives.length,d.fresh&&state.monitorSource==='latest'?tot.points.at(-1)?.[1]:null,drives.reduce((s,x)=>s+(finite(d.cap?.[x.id])&&d.cap[x.id]>0?d.cap[x.id]:barScale.ceiling),0)):''}</div>`:empty(state.monitorShowSSD?'No host SSD counters':'No chart layer selected',state.monitorShowSSD?(d.timeline_note||'The arm has not recorded physical-drive samples yet.'):'Enable SSDs or Monitor Engram above.'))+
    ((d.kind==='ds4-bench'||d.kind==='task')&&!d.memory?'':`<div class="metrics">${metric('RAM available',unit(mem.available_gib,'GiB'),'Latest saved system sample','memory')}${metric('RAM used',unit(mem.used_gib,'GiB'),'Latest saved system sample','memory')}${metric('GPU activity',unit(mem.gpu_percent,'%'),'Latest saved system sample','bolt')}${metric('Prompt processing',unit(summary.ds4_prefill_tps,'tok/s'),'Engine’s final timer','clock')}</div>`)+
    (d.kind==='ds4-bench'||d.kind==='task'?'':panel('M1 expert node','Completed arm counters. The current harness does not stream remote SSD or network rates.',`<div class="panel-body">${remote.mode?`<div class="summary-strip"><span>${badge(remote.mode==='remote'?'Remote arm':'Local control')} ${remote.node_stopped?'Node stopped at campaign end':''}</span><span>${remote.gets??'—'} remote requests</span></div><div class="cluster-metrics">${metric('Remote payload',unit(finite(remote.bytes_sent)?remote.bytes_sent/1e9:null,'GB'),'Whole arm · application bytes','disk')}${metric('Node cache hit rate',unit(remoteCount?100*remote.cache_hits/remoteCount:null,'%'),'Request hits ÷ hits + misses','memory')}${metric('Device I/O',unit(deviceIO,'MB'),finite(deviceIO)?'iostat reported MB · reads + writes':'Not recorded by this campaign','disk')}</div><div class="cluster-predictions">${links.map(([name,bytes])=>`<div class="cluster-prediction"><span>${esc(name)}<small>M1 transmitted bytes</small></span><div class="cluster-bar-track"><i style="width:${Math.max(0,Math.min(100,bytes/maxBytes*100))}%"></i></div><strong>${fmt(bytes/1e9)} <small>GB</small></strong></div>`).join('')}</div>`:'<p>Remote counters arrive after the arm is summarized. No live remote rates are available.</p>'}<p class="chart-note">Device I/O includes all reads and writes on the M1 disk; it cannot identify expert-read bytes. Application read bytes are separate. Interface totals include network overhead and management traffic.</p></div>`))+
    `<details class="panel" style="padding:18px"><summary>Measurement source</summary><p class="source-path">${esc(d.log)}</p><p>ARGODRIVE reads existing files only. Opening this page does not start inference, SSH probes or hardware collection. The task recorder or benchmark runner supplies its own samples. Host charts use recorded counter intervals over the selected ${state.liveWindow}-second window; averages use actual durations. Missing metrics stay unavailable.</p><p>Keep writing future arms under <code>${esc(d.runs_path)}</code> and select Follow latest test. Benchmark validity: ${esc(summary.valid||'not reported yet')}.</p></details>`;
  if($('#monitor-cursor')){
    $('#monitor-cursor').oninput=e=>{$('#monitor-position').textContent=fmt(Number(e.target.value),1)+'s';};
    $('#monitor-cursor').onchange=e=>{state.monitorCursor=Number(e.target.value);updateHarness(d);};
    $('#monitor-latest').onclick=()=>{state.monitorCursor=null;updateHarness(d);};
  }

}
setInterval(()=>{if(!document.hidden&&isTestMonitor()&&!document.activeElement?.matches('#harness-arm,#monitor-source,#live-window,#monitor-cursor'))loadHarness();},2000);

function livePlot(d){
  const series=(d.devices||[]).filter(x=>x.present).map((x,i)=>({name:x.label||x.id,color:devColor(x.id,i),points:d.traces?.[x.id]||[]}));
  if(d.total?.length)series.push({name:'Aggregate',color:'var(--accent)',points:d.total});
  const points=series.flatMap(s=>s.points);if(!points.length)return empty('Waiting for drive samples','Throughput appears when the hardware sampler supplies fresh counters.','','disk');
  const end=Math.max(...points.map(p=>p[0])),start=end-120,max=Math.max(1,d.cap_total||0,...points.map(p=>p[1]))*1.1,w=900,h=210,left=42,top=15;
  return `<svg class="plot" viewBox="0 0 960 255" role="img" aria-label="Drive read throughput over the last 120 seconds in gigabytes per second">${[0,1,2,3,4].map(i=>`<line class="gridline" x1="${left}" x2="${left+w}" y1="${top+h-h*i/4}" y2="${top+h-h*i/4}"/><text x="${left-10}" y="${top+h-h*i/4+4}" text-anchor="end">${fmt(max*i/4,0)}</text>`).join('')}${[0,30,60,90,120].map(t=>`<text x="${left+w*t/120}" y="249" text-anchor="middle">${t===120?'latest':t-120+'s'}</text>`).join('')}${series.map(s=>`<path d="${chartPath(s.points.filter(p=>p[0]>=start),w,h,start,end,max)}" transform="translate(${left},${top})" stroke="${s.color}" stroke-width="${s.name==='Aggregate'?2.6:1.6}" fill="none"/>`).join('')}</svg><div class="chart-legend">${series.map(s=>`<span><span class="legend-dot" style="background:${s.color}"></span>${esc(s.name)}</span>`).join('')}</div>`;
}
function attributionData(data){return data?.attribution||data?.streaming_attribution||{};}
function attributionSeries(data,id,kind){const attr=attributionData(data);return attr?.devices?.[id]?.[kind]||attr?.[id]?.[kind]||[];}
function hasAttribution(data,devices=[]){return devices.some(d=>attributionSeries(data,d.id,'weights').length||attributionSeries(data,d.id,'engram').length);}
function driveReadBars(points,color,scale,seconds=20, data=null, id=null, style='bars'){
  const {end,ceiling}=scale,start=end-seconds,left=32,width=596,base=132,height=116;
  const stats=readWindowStats(points||[],seconds,end),y=v=>base-v/ceiling*height;
  const monitorState=typeof state==='undefined'?{monitorShowSSD:true,monitorShowEngram:false}:state;
  const classified=monitorState.monitorShowEngram&&id&&hasAttribution(data,[{id}]);
  const bars=monitorState.monitorShowSSD ? (classified ? `<g opacity=".2">${stats.points.map(p=>{const x=left+(p[0]-p[2]-start)/seconds*width,h=p[1]/ceiling*height;return `<rect x="${x}" y="${y(p[1])}" width="${Math.max(.3,p[2]/seconds*width*.85)}" height="${h}" fill="${color}"><title>Total device reads · ${fmt(p[1])} GB/s</title></rect>`;}).join('')}</g>` : stats.points.map(p=>{const x=left+(p[0]-p[2]-start)/seconds*width,h=p[1]/ceiling*height;return `<rect x="${x}" y="${y(p[1])}" width="${Math.max(.3,p[2]/seconds*width*.85)}" height="${h}" fill="${color}" opacity=".78"><title>${fmt(p[1])} GB/s · ${fmt(end-p[0],1)} seconds ago · ${fmt(p[2]*1000,0)} ms interval</title></rect>`;}).join('')) : '';
  const classBars=classified?['weights','engram'].map(kind=>attributionSeries(data,id,kind).filter(p=>Array.isArray(p)&&finite(p[0])&&finite(p[1])&&finite(p[2])&&p[2]>0).map(p=>{const x=left+(p[0]-p[2]-start)/seconds*width,h=p[1]/ceiling*height,c=kind==='engram'?'var(--purple)':'var(--teal)';return `<rect x="${x}" y="${y(p[1])}" width="${Math.max(.3,p[2]/seconds*width*.85)}" height="${h}" fill="${c}" opacity=".82"><title>${kind==='engram'?'Engram rows':'Weight tensors'} · ${fmt(p[1])} GB/s · ${fmt(p[2]*1000,0)} ms</title></rect>`;}).join('')).join(''):'';
  // Line mode: one polyline through the interval midpoints with a light fill
  // underneath. Used by the Combined card, where a continuous total reads more
  // naturally than a stack of bars; per-drive cards keep bars.
  const series=(()=>{if(style!=='line'||!monitorState.monitorShowSSD)return '';const pts=stats.points.slice().sort((a,b)=>a[0]-b[0]);if(!pts.length)return '';const xy=pts.map(p=>[left+(p[0]-p[2]/2-start)/seconds*width,y(p[1])]);const path=xy.map(([x,v])=>`${x.toFixed(1)},${v.toFixed(1)}`).join(' ');return `<polygon points="${xy[0][0].toFixed(1)},${base} ${path} ${xy[xy.length-1][0].toFixed(1)},${base}" fill="${color}" opacity=".14"/><polyline points="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"><title>${pts.length} intervals · peak ${fmt(stats.peak)} GB/s</title></polyline>`;})();
  const grid=[0,.25,.5,.75,1].map(f=>`<line class="gridline" x1="${left}" x2="${left+width}" y1="${y(ceiling*f)}" y2="${y(ceiling*f)}"/><text x="${left-9}" y="${y(ceiling*f)+4}" text-anchor="end">${fmt(ceiling*f,0)}</text>`).join('');
  const ticks=[0,.25,.5,.75,1].map(f=>`<text x="${left+width*f}" y="154" text-anchor="${f===0?'start':f===1?'end':'middle'}">${f===1?'latest':'−'+fmt(seconds*(1-f),seconds===10?1:0)+'s'}</text>`).join('');
  const line=(value,kind,dash='')=>finite(value)?`<line class="live-${kind}-line" x1="${left}" x2="${left+width}" y1="${y(value)}" y2="${y(value)}" stroke="${kind==='average'?'var(--text)':color}" stroke-width="${kind==='average'?2:1.5}" ${dash?`stroke-dasharray="${dash}"`:''}/>`:'';
  return `<svg class="drive-history live-drive-history" viewBox="0 0 640 164" preserveAspectRatio="none" role="img" aria-label="Read throughput over the last ${seconds} seconds; ${scale.mode==='individual'?'individual':'shared'} scale zero to ${fmt(ceiling,0)} gigabytes per second; average ${fmt(stats.mean)}; window peak ${fmt(stats.peak)}">${grid}${style==='line'?series:bars}${classBars}${line(monitorState.monitorShowSSD?stats.peak:null,'peak','5 4')}${line(monitorState.monitorShowSSD?stats.mean:null,'average')}${ticks}</svg><div class="live-chart-facts">${monitorState.monitorShowSSD?`<span><i class="live-line-key"></i>Average <strong>${fmt(stats.mean)} <small>GB/s</small></strong></span><span><i class="live-line-key peak" style="border-color:${color}"></i>Peak <strong>${fmt(stats.peak)} <small>GB/s</small></strong></span>`:''}${classified?`<span class="monitor-class-fact weights">Weights <strong>${fmt(readWindowStats(attributionSeries(data,id,'weights'),seconds,end).mean)} <small>GB/s</small></strong></span><span class="monitor-class-fact engram">Engram <strong>${fmt(readWindowStats(attributionSeries(data,id,'engram'),seconds,end).mean)} <small>GB/s</small></span>`:''}<span class="monitor-sampling-detail" title="${stats.points.length} intervals; duration-weighted average">${fmt(stats.seconds,1)}s sampled</span></div>`;
}
function combinedReadCard(points,scale,seconds,count,current=null,capTotal=null){
  const stats=readWindowStats(points||[],seconds,scale.end);
  const peak=Math.max(0,...stats.points.map(p=>p[1]));
  /* Match the per-drive axis so the two are directly comparable: a combined
   * chart that auto-scales while each drive is pinned to a fixed ceiling makes
   * the total look SMALLER than one of its parts. Fall back to the old
   * auto-scale only if the aggregate would overflow the fixed axis. */
  // Axis = the drives' combined calibrated capacity (sum of per-drive ceilings), so
  // the line reads as utilisation of what the disks can actually deliver together.
  // Falls back to the fixed per-drive axis x count when no ceilings are calibrated.
  const fixedTotal=(finite(capTotal)&&capTotal>0)?capTotal:scale.ceiling*Math.max(1,count);
  const totalScale={end:scale.end,ceiling:peak>fixedTotal?Math.ceil(peak*1.15/4)*4:fixedTotal};
  const chart=driveReadBars(points,'var(--accent)',totalScale,seconds,null,null,'line').replace('Read throughput over','Combined read throughput over').replace('shared scale zero','aggregate scale zero');
  return `<section class="device-card live-drive-card combined-drive-card" aria-label="Combined SSD reads"><div class="live-drive-head"><div><h3>Combined</h3><p>${count} SSDs · 0–${fmt(totalScale.ceiling,0)} GB/s</p></div>${finite(current)?`<div class="metric-value" title="Current aggregate read rate">${fmt(current)} <span class="unit">GB/s</span></div>`:''}</div>${chart}</section>`;
}

function ssdSeries(d){
  const end=Math.max(0,...Object.values(d.read_windows||{}).flatMap(s=>s.map(p=>p[0])));
  return (d.devices||[]).filter(x=>x.present).map((x,i)=>({...x,color:devColor(x.id,i),
    stats:readWindowStats(d.read_windows?.[x.id]||[],state.ssdWindow,end),rate:state.connected?d.cur?.[x.id]:null}));
}
function ssdChart(x,max){
  const {points,end,mean,peak}=x.stats,w=620,h=142,left=32,top=12,start=end-state.ssdWindow;
  if(!points.length)return empty('Waiting for samples','This drive has no measured intervals in the selected window.','','disk');
  const y=v=>top+h-h*v/max;
  const line=(v,color,dash='')=>finite(v)?`<line x1="${left}" x2="${left+w}" y1="${y(v)}" y2="${y(v)}" stroke="${color}" stroke-width="1.2" ${dash?`stroke-dasharray="${dash}"`:''}/>`:'';
  return `<svg class="ssd-plot" viewBox="0 0 680 186" role="img" aria-label="${esc(x.label)} read-throughput bars, average ${fmt(mean)} and window peak ${fmt(peak)} gigabytes per second">
    ${[0,.5,1].map(f=>`<line class="gridline" x1="${left}" x2="${left+w}" y1="${y(max*f)}" y2="${y(max*f)}"/><text x="${left-7}" y="${y(max*f)+4}" text-anchor="end">${fmt(max*f,0)}</text>`).join('')}
    ${points.map(p=>`<rect x="${left+(p[0]-p[2]-start)/state.ssdWindow*w}" y="${y(p[1])}" width="${Math.max(.3,p[2]/state.ssdWindow*w*.8)}" height="${p[1]/max*h}" fill="${x.color}" opacity=".7"/>`).join('')}
    ${line(mean,'var(--text)')}${line(peak,x.color,'4 4')}${line(x.ceiling_gbps,'var(--muted)','2 5')}
    <text x="${left}" y="179">−${state.ssdWindow}s</text><text x="${left+w/2}" y="179" text-anchor="middle">−${state.ssdWindow/2}s</text><text x="${left+w}" y="179" text-anchor="end">latest</text></svg>`;
}

function renderTopology(){
  $('#main').innerHTML=heading('Connection map','Mac, ports and storage paths.',button('Rescan connections','rescan-topology','','refresh')+button('Export map','export-topology','','download'))+
    `<div class="topo-toolbar"><div class="topo-status" id="topology-status">Discovering hardware…</div><label><input type="checkbox" id="topology-empty" ${state.topologyEmpty?'checked':''}> Show empty ports</label><label>Zoom <select id="topology-zoom">${['fit',75,100,125,150].map(v=>`<option value="${v}" ${state.topologyZoom===v?'selected':''}>${v==='fit'?'Fit to window':v+'%'}</option>`).join('')}</select></label></div><div id="topology-content"></div>`;
  $('#topology-empty').onchange=e=>{state.topologyEmpty=e.target.checked;if(!state.topologyEmpty&&state.topology?.nodes.some(n=>n.id===state.topologySelected&&n.kind==='port'&&!n.connected))state.topologySelected='mac';updateTopology({recenter:true});};
  $('#topology-zoom').onchange=e=>{state.topologyZoom=e.target.value==='fit'?'fit':Number(e.target.value);updateTopology({recenter:true});};
  if(state.topology)updateTopology();
  loadTopology();
}
async function loadTopology(force=false){
  if(state.topologyLoading)return;state.topologyLoading=true;
  const el=$('[data-action="rescan-topology"]');if(el)el.disabled=true;
  try{state.topology=await api('/topology'+(force?'?refresh=1':''));if(state.route==='topology')updateTopology();}
  catch(e){if(state.route==='topology'){$('#topology-status').textContent='Discovery unavailable';$('#topology-content').innerHTML=empty('Could not read connections',e.message,button('Try again','rescan-topology','primary'));}}
  finally{state.topologyLoading=false;const el=$('[data-action="rescan-topology"]');if(el)el.disabled=false;}
}
function updateTopology({recenter=false}={}){
  if(state.route!=='topology'||!state.topology||!$('#topology-content'))return;
  const t=state.topology,d={...state.data,health:{...state.data.health,scope:state.connected&&state.data.health?.scope}},opts={selected:state.topologySelected,showEmpty:state.topologyEmpty,zoom:state.topologyZoom};
  const age=t.collected_at?Math.max(0,(Date.now()/1000-t.collected_at)):null;
  $('#topology-status').innerHTML=`${badge(t.mode==='snapshot'?'Snapshot · sampling off':d.health?.scope?'Live read metrics':'Read metrics unavailable',t.mode==='snapshot'?'':d.health?.scope?'green':'amber')}<span>${t.ports||0} ports · ${t.nodes.filter(n=>n.kind==='hub').length} hubs · ${t.nodes.filter(n=>n.kind==='enclosure').length} enclosures</span><span class="muted">Wiring scanned ${age===null?'—':age<60?'just now':Math.floor(age/60)+'m ago'}</span>`;
  if(!t.nodes.length){$('#topology-content').innerHTML=empty('No topology available','Rescan connections after checking the hardware.');return;}
  const contents=`${(t.errors||[]).map(e=>`<p class="data-error">${esc(e)}</p>`).join('')}<div class="topology-workspace"><section class="panel topology-map"><div class="topology-canvas" id="topology-canvas" tabindex="0" role="region" aria-label="Connection map. Select a device to inspect it; scroll to pan when zoomed."><div class="topology-stage ${state.topologyZoom==='fit'?'is-fit':''}">${topologySVG(t,d,opts)}</div></div><div class="topology-legend"><span><i class="topo-line-key"></i>Detected connection</span><span><i class="topo-line-key dashed"></i>Empty / unresolved</span><span>Gb/s: link · GB/s: reads</span><span class="topology-map-hint">Select a device to inspect · scroll to pan when zoomed</span></div></section><aside class="panel topology-inspector">${topologyInspector(t,d,opts)}</aside></div>${panel('Connections','Read timing follows downstream storage; it is not an added delay at each link.',topologyConnections(t,d,opts))}<p class="chart-note">Port numbers come from macOS and do not label left/right sockets. Rescan after changing cables. Read time includes storage-driver processing; it is not cable round-trip latency or GPU wait. ${t.nodes.some(n=>n.kind==='hub')?'Downstream devices share their hub’s upstream connection.':'No hub is detected in the current wiring.'}</p>`;
  // Refresh metrics without moving a zoomed map or stealing keyboard focus.
  const previous=$('#topology-canvas'),x=previous?.scrollLeft||0,y=previous?.scrollTop||0;
  const focused=document.activeElement?.closest('[data-topology-node]');
  const focusId=focused?.dataset.topologyNode,focusTag=focused?.tagName;
  $('#topology-content').innerHTML=contents;
  const canvas=$('#topology-canvas');
  canvas.scrollLeft=recenter||!previous?(canvas.scrollWidth-canvas.clientWidth)/2:x;
  canvas.scrollTop=recenter||!previous?(canvas.scrollHeight-canvas.clientHeight)/2:y;
  if(focusId){const target=[...document.querySelectorAll('[data-topology-node]')].find(n=>n.dataset.topologyNode===focusId&&n.tagName===focusTag);target?.focus({preventScroll:true});}
}
function exportTopology(){
  const svg=$('.topology-svg');if(!svg){toast('Discover a connection map first.');return;}
  const copy=svg.cloneNode(true),original=[svg,...svg.querySelectorAll('*')],cloned=[copy,...copy.querySelectorAll('*')];
  for(let i=0;i<original.length;i++){
    const css=getComputedStyle(original[i]);
    for(const k of ['fill','stroke','stroke-width','stroke-dasharray','stroke-linecap','font-size','font-family','font-weight','color','opacity'])cloned[i].style.setProperty(k,css.getPropertyValue(k));
  }
  const v=svg.viewBox.baseVal;copy.style.width=v.width+'px';copy.style.height=v.height+'px';copy.style.minWidth='0';copy.setAttribute('width',v.width);copy.setAttribute('height',v.height);
  download('argodrive-topology.svg',new XMLSerializer().serializeToString(copy),'image/svg+xml');
}

function renderSSDs(){
  const d=state.data,connected=ssdSeries(d),shown=connected.filter(x=>state.ssdFilter==='connected'||(state.connected&&d.health?.scope&&x.stats.reading)),fresh=state.connected&&d.health?.scope;
  const total=readWindowStats(d.read_windows?.TOTAL||[],state.ssdWindow),sharedMax=Math.max(1,...shown.flatMap(x=>[x.stats.peak||0,x.ceiling_gbps||0,...x.stats.points.map(p=>p[1])]))*1.08;
  $('#main').innerHTML=heading('SSD activity','Connected physical drives · measured reads',button(state.paused?'Resume display':'Pause display','pause-live','',state.paused?'play':'pause')+button('Export window','export-ssd','','download'))+
    (d.mode==='reports'?panel('Live collection is off','',empty('Enable hardware collection','Use Monitor → Live Hardware in the Mac app to show connected SSDs.',linkButton('Collection settings','settings','primary'))):
    `<div class="ssd-toolbar"><label>Show <select id="ssd-filter"><option value="connected" ${state.ssdFilter==='connected'?'selected':''}>Connected drives</option><option value="reading" ${state.ssdFilter==='reading'?'selected':''}>Reading now</option></select></label><label>Window <select id="ssd-window">${[30,60,120].map(n=>`<option value="${n}" ${state.ssdWindow===n?'selected':''}>${n} seconds</option>`).join('')}</select></label><label>Scale <select id="ssd-scale"><option value="shared" ${state.ssdScale==='shared'?'selected':''}>Shared</option><option value="device" ${state.ssdScale==='device'?'selected':''}>Per drive</option></select></label><span>${badge(state.paused?'Display paused':fresh?'Live counters':'Stale / unavailable',fresh&&!state.paused?'green':'amber')}</span></div>`+
    `<div class="ssd-summary"><div><span>Connected total</span><strong>${unit(state.connected?d.cur_total:null,'GB/s')}</strong></div><div><span>Window average</span><strong>${unit(total.mean,'GB/s')}</strong></div><div><span>Window peak</span><strong>${unit(total.peak,'GB/s')}</strong></div><div><span>Drives shown</span><strong>${shown.length} <small>/ ${connected.length}</small></strong></div></div>`+
    (shown.length?`<div class="ssd-grid">${shown.map(x=>{const max=state.ssdScale==='shared'?sharedMax:Math.max(1,x.ceiling_gbps||0,...x.stats.points.map(p=>p[1]))*1.08;return `<section class="panel ssd-card"><div class="ssd-card-head"><div><h2><span class="legend-dot" style="background:${x.color}"></span>${esc(x.label||x.id)}</h2><p>${esc(x.connection)} · ${esc(x.device)}</p></div><strong>${unit(x.rate,'GB/s')}</strong></div><div class="ssd-facts"><span>Average <b>${fmt(x.stats.mean)}</b></span><span>Window peak <b>${fmt(x.stats.peak)}</b></span><span>Session peak <b>${finite(d.peaks?.[x.id])&&x.stats.points.length?fmt(d.peaks[x.id]):'—'}</b></span></div>${ssdChart(x,max)}<div class="ssd-card-foot"><span>Solid line: average · dashed: peak</span><span>${finite(x.ceiling_gbps)?'Ceiling '+fmt(x.ceiling_gbps)+' GB/s':'Ceiling uncalibrated'}</span></div><p class="chart-note">${fmt(x.stats.seconds,1)} seconds measured in this window · average includes sampled idle time</p></section>`;}).join('')}</div>`:
    panel('No drives match this view','',empty(connected.length?'No SSD reads right now':'No connected drives detected',connected.length?'Switch to Connected drives to keep idle SSDs visible.':'Check the drive connection and hardware collection.',connected.length?button('Show connected drives','show-connected','primary'):'','disk')))+
    `<p class="chart-note">${state.paused?'Display frozen; hardware collection continues. ':''}Reading now means measured reads above 1 MB/s in the last 3 seconds. Averages use actual interval durations; peaks exclude delayed intervals over 350 ms. Aggregate uses simultaneous samples.</p>`);
  $('#main').insertAdjacentHTML('beforeend',`<div id="spotlight-content">${spotlightPanel(spotlightData,spotlightLoading,spotlightError)}</div>`);
  if(spotlightData===null&&!spotlightLoading&&!spotlightError)loadSpotlight();
  if($('#ssd-filter')){
    $('#ssd-filter').onchange=e=>{state.ssdFilter=e.target.value;renderSSDs();};
    $('#ssd-window').onchange=e=>{state.ssdWindow=Number(e.target.value);renderSSDs();};
    $('#ssd-scale').onchange=e=>{state.ssdScale=e.target.value;renderSSDs();};
  }
}
let spotlightData=null,spotlightLoading=false,spotlightError='',spotlightPollBusy=false;
// Older running previews re-read app.js but cannot serve newly added modules.
// Keep their existing charts usable until the backend is restarted.
let spotlightPanel=()=>'<section class="panel"><div class="panel-body"><h2>Spotlight indexing</h2><p class="chart-note">Opening drive controls…</p></div></section>';
let spotlightConfirmation=()=>'';
import('/spotlight-view.js').then(view=>{spotlightPanel=view.spotlightPanel;spotlightConfirmation=view.spotlightConfirmation;updateSpotlight();}).catch(()=>{
  spotlightPanel=()=>'<section class="panel"><div class="panel-body"><h2>Spotlight indexing</h2><p class="chart-note">Restart this older ARGODRIVE backend to load the new Spotlight controls. Existing charts remain available.</p></div></section>';updateSpotlight();
});
function updateSpotlight(){if($('#spotlight-content'))$('#spotlight-content').innerHTML=spotlightPanel(spotlightData,spotlightLoading,spotlightError);}
async function loadSpotlight(scan=false){
  if(spotlightLoading)return;
  spotlightLoading=true;spotlightError='';updateSpotlight();
  try{
    spotlightData=await api(scan?'/spotlight/scan':'/spotlight',scan?{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:'{}'}:{});
  }catch(e){spotlightError=e.message;}
  finally{spotlightLoading=false;updateSpotlight();}
}
function requestSpotlightChange(id,enabled){
  const row=spotlightData?.volumes?.find(r=>r.id===id);
  if(!row?.can_change||spotlightLoading||spotlightData?.job?.status==='running')return;
  const dialog=$('#run-dialog');
  $('#detail-content').innerHTML=`<div class="panel-head"><h2 id="detail-title">${enabled?'Enable':'Disable'} Spotlight indexing</h2><button class="button small" id="spotlight-dismiss">Cancel</button></div><div class="panel-body"><p class="spotlight-confirm">${esc(spotlightConfirmation(row,enabled))}</p><div class="button-group"><button class="button primary" id="spotlight-confirm">${enabled?'Turn indexing on':'Turn indexing off'}</button></div></div>`;
  dialog.showModal();$('#spotlight-dismiss').onclick=()=>dialog.close();
  $('#spotlight-confirm').onclick=async()=>{
    dialog.close();spotlightLoading=true;spotlightError='';updateSpotlight();
    try{spotlightData=await api('/spotlight',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:JSON.stringify({id,enabled})});}
    catch(e){spotlightError=e.message;}
    finally{spotlightLoading=false;updateSpotlight();}
  };
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-spotlight-scan]'))loadSpotlight(true);
  const target=e.target.closest('[data-spotlight-volume]');
  if(target)requestSpotlightChange(target.dataset.spotlightVolume,target.dataset.spotlightEnabled==='true');
});
setInterval(async()=>{
  if(document.hidden||spotlightLoading||spotlightPollBusy||!spotlightData||
    (spotlightData.job?.status!=='running'&&!spotlightData.scanning))return;
  spotlightPollBusy=true;
  try{spotlightData=await api('/spotlight');spotlightError='';updateSpotlight();}
  catch(e){spotlightError=e.message;updateSpotlight();}
  finally{spotlightPollBusy=false;}
},2000);
function exportSSD(){
  const d=state.data,rows=['device,interval_end_unix_s,interval_seconds,read_gb_s'];
  const ids=new Set(ssdSeries(d).filter(x=>state.ssdFilter==='connected'||x.stats.reading).map(x=>x.id));ids.add('TOTAL');
  for(const id of ids)for(const p of readWindowStats(d.read_windows?.[id]||[],state.ssdWindow).points)rows.push([id,p[0],p[2],p[1]].join(','));
  download('argodrive-ssd-window.csv',rows.join('\n')+'\n','text/csv');
}
function evidenceBadge(source){return badge(source==='engine'?'Engine confirmed':source==='requested'?'Requested':'Unknown',source==='engine'?'green':source==='requested'?'amber':'');}
let engineEditor=null,engineEditorError='';
const engineEditorReady=import('/engine-settings.js').then(m=>{engineEditor=m;if(state.route==='streaming'&&state.data&&state.settings)renderStreaming();}).catch(()=>{engineEditorError='Restart ARGODRIVE with the updated local build to load ds4 settings. Recorded arms are still available.';if(state.route==='streaming'&&state.data&&state.settings)renderStreaming();});
let modelSupport=null;
import('/model-support.js').then(m=>{modelSupport=m;if(state.route==='streaming'&&state.streamingView==='models')renderStreaming();}).catch(()=>{});
let ssdTunerModule=null;
async function loadSSDTuner(){
  try{ssdTunerModule ||= await import('/ssd-tuner-view.js');if(state.route==='streaming'&&state.streamingView==='ssd-test')ssdTunerModule.mountSSDTuner($('#ssd-tuner-content'),{api,download,token:state.settings.token,onVerify:()=>{location.hash='#monitor';setTimeout(openMonitorTest,100);}});}
  catch(e){if($('#ssd-tuner-content'))$('#ssd-tuner-content').textContent='SSD tuner unavailable: '+e.message;}
}
function renderStreaming(){
  if(!findRun(state.streamingRun))state.streamingRun=runId(state.rows.find(r=>!r.incomplete)||state.rows[0]||{block:'',arm:''});
  const r=findRun(state.streamingRun),edit=state.streamingView==='engine',campaigns=state.streamingView==='campaigns',models=state.streamingView==='models',ssdTest=state.streamingView==='ssd-test';
  $('#main').innerHTML=heading('Engine & SSD streaming','Configure the engine, then qualify settings for your model and workload.')+
    `<div class="diagnostic-tabs" role="tablist" aria-label="Streaming views"><button role="tab" aria-selected="${models}" class="${models?'active':''}" data-streaming-view="models">Models & readiness</button><button role="tab" aria-selected="${edit}" class="${edit?'active':''}" data-streaming-view="engine">ds4 settings</button><button role="tab" aria-selected="${!edit&&!campaigns&&!models&&!ssdTest}" class="${!edit&&!campaigns&&!models&&!ssdTest?'active':''}" data-streaming-view="recorded">Recorded arms</button><button role="tab" aria-selected="${campaigns}" class="${campaigns?'active':''}" data-streaming-view="campaigns">Tuning campaigns</button><button role="tab" aria-selected="${ssdTest}" class="${ssdTest?'active':''}" data-streaming-view="ssd-test">5-minute SSD test</button></div>`+
    (ssdTest?'<div id="ssd-tuner-content"><p class="muted">Opening SSD tuner…</p></div>':models?'<div id="model-support-content"><p class="muted">Loading model support…</p></div>':campaigns?'<div id="campaign-content"><p class="muted">Reading campaign records…</p></div>':edit?`<div id="engine-settings-editor">${engineEditorError?`<p class="data-error">${esc(engineEditorError)}</p>`:'<p class="muted">Loading engine controls…</p>'}</div>`:
    (r?`<div class="streaming-selector"><label for="streaming-run">Recorded arm</label><select id="streaming-run">${state.rows.map(x=>`<option value="${esc(runId(x))}" ${runId(x)===state.streamingRun?'selected':''}>${esc(x.arm)} · ${esc(x.engine)} · ${length(x)} tokens${x.incomplete?' · incomplete':''}</option>`).join('')}</select></div><div class="source-strip"><span>Saved result · ${esc(r.ran)} · ${esc(shortModel(r))}</span>${evidenceBadge(r.streaming_status)}</div><div id="streaming-content"><p class="muted">Reading the arm and its engine declarations…</p></div>`:
    panel('Connect recorded arms','',empty('Choose your benchmark folder','Runs are recorded by the engine harness, then read here with their settings and evidence.',linkButton('Choose run folder','settings','primary')))));
  for(const tab of document.querySelectorAll('[data-streaming-view]'))tab.onclick=()=>{state.streamingView=tab.dataset.streamingView;renderStreaming();};
  if(ssdTest){loadSSDTuner();return;}
  if(models){if(modelSupport)modelSupport.mountModelSupport($('#model-support-content'),{api,download,token:state.settings.token});else $('#model-support-content').textContent='Model view is loading. If unavailable, restart with the current build.';return;}
  if(campaigns){loadCampaigns();return;}
  if(edit){if(engineEditor)engineEditor.mountEngineSettings($('#engine-settings-editor'),{savedDraft:state.settings.engine_draft,saveDraft:async draft=>{const result=await api('/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:JSON.stringify({engine_draft:draft})});state.settings.engine_draft=result.engine_draft;}});return;}
  if(!r)return;
  $('#streaming-run').onchange=e=>{state.streamingRun=e.target.value;renderStreaming();};
  loadStreaming(r);
}
let campaignModule=null,campaignData=null,campaignSelected='',campaignLoading=false;
async function loadCampaigns(){
  if(campaignLoading)return;campaignLoading=true;
  try{
    const source=state.settings?.runs;
    if(!campaignModule)campaignModule=await import('/campaign-view.js');
    const data=await api('/campaigns');
    if(state.route!=='streaming'||state.streamingView!=='campaigns'||source!==state.settings?.runs)return;
    campaignData=data;updateCampaigns();
  }catch(e){if($('#campaign-content'))$('#campaign-content').innerHTML=panel('Campaign view unavailable','',`<div class="panel-body"><p class="data-error">${esc(e.message)}</p><p class="chart-note">An older running backend needs a restart to load the campaign reader. Existing runs and SSD charts remain available.</p></div>`);}
  finally{campaignLoading=false;}
}
function updateCampaigns(){
  const host=$('#campaign-content');if(!host||!campaignModule||!campaignData)return;
  const selected=campaignModule.selectedCampaign(campaignData,campaignSelected);
  host.innerHTML=campaignModule.campaignView(campaignData,{selected:selected?.id});
  if($('#campaign-select'))$('#campaign-select').onchange=e=>{campaignSelected=e.target.value;updateCampaigns();};
  const exportButton=$('[data-campaign-export]');
  if(exportButton)exportButton.onclick=()=>download('argodrive-campaign-review.json',campaignModule.campaignExport(selected),'application/json');
}
setInterval(()=>{if(!document.hidden&&state.route==='streaming'&&state.streamingView==='campaigns'&&!document.activeElement?.closest('#campaign-content button,#campaign-content select,#campaign-content details'))loadCampaigns();},5000);
async function loadStreaming(r){
  const id=runId(r);
  try{
    const d=await detailFor(r);if(state.route!=='streaming'||state.streamingView!=='recorded'||state.streamingRun!==id)return;
    const p=d.streaming||{method:'Not recorded',fields:[],homes:[]};
    $('#streaming-content').innerHTML=`<div class="method-banner"><div><span class="eyebrow">RECORDED READ METHOD</span><h2>${esc(p.method)}</h2><p>${esc(p.note||'Streaming evidence is unavailable in this backend version.')}</p></div>${evidenceBadge(p.status)}</div>`+
    `<div class="two-col equal">${panel('Streaming settings','Requested values and engine confirmations stay separate.',`<div class="table-scroll"><table class="tight-table"><thead><tr><th>Setting</th><th>Value</th><th>Evidence</th></tr></thead><tbody>${p.fields.map(f=>`<tr><td>${esc(f.label)}</td><td>${esc(f.value)}</td><td>${evidenceBadge(f.source)}<details><summary>Source</summary><code class="path-text">${esc(f.evidence)}</code></details></td></tr>`).join('')}</tbody></table></div>`)}${panel('Weight locations','File descriptors may share the same physical SSD.',`<div class="panel-body">${p.homes.length?p.homes.map(h=>`<div class="weight-home"><strong>${esc(h.role)} ${h.weight?'· weight '+esc(h.weight):''}</strong>${evidenceBadge(h.source)}<code class="path-text">${esc(h.path)}</code></div>`).join(''):'<p class="muted">Weight locations were not recorded.</p>'}<p class="chart-note">RAID0 combines devices into a striped volume. Replica split reads divide requests across file copies. Dual-home placement gives an expert two locations; the scheduling policy determines how they are read.</p></div>`)}</div>`+
    panel('Arm files','These files are read in place. The dashboard does not create an inference arm when opened.',`<div class="panel-body"><label class="field"><span>Recorded in</span><input readonly value="${esc(d.folder||'')}" aria-label="Arm folder"></label><div class="button-group">${button('Export arm manifest','export-arm-files','','download')}<button class="button" data-detail="${esc(id)}">Run details</button></div></div><div class="table-scroll"><table class="tight-table"><thead><tr><th>File</th><th>Contains</th><th>Size</th></tr></thead><tbody>${(d.files||[]).map(f=>`<tr><td title="${esc(f.path)}"><code>${esc(f.name)}</code></td><td>${esc(f.kind)}</td><td>${fmt(f.bytes/1e6,2)} MB</td></tr>`).join('')}</tbody></table></div>`)+
    panel('Engine declarations','Read-mode and scheduler evidence from this arm.',`<div class="panel-body"><pre>${esc((p.engine_declarations||[]).join('\n')||'No supported engine declarations found.')}</pre></div>`);
  }catch(e){if(state.route==='streaming'&&$('#streaming-content'))$('#streaming-content').innerHTML=`<p class="data-error">${esc(e.message)}</p>`;}
}
async function exportArmFiles(){const r=findRun(state.streamingRun);if(!r)return;try{const d=await detailFor(r);download('argodrive-'+r.arm+'-manifest.json',JSON.stringify({block:r.block,arm:r.arm,folder:d.folder,files:d.files,streaming:d.streaming},null,2),'application/json');}catch(e){toast(e.message);}}

function renderLive(){
  const barScale=monitorReadBarScale((state.data.devices||[]).filter(x=>x.present),state.data.read_windows,state.liveWindow);
  const d=state.data,report=d.mode==='reports',fresh=d.system_fresh,p=d.live_progress||{},devices=(d.devices||[]).filter(x=>x.present),sys=d.sys||{},canRate=state.connected&&finite(p.rate),scope=d.health?.scope&&state.connected;
  $('#main').innerHTML=monitorHeading()+
    (report?`<section class="panel">${empty('Live collection is off','You are reviewing saved reports. Start ARGODRIVE in live mode to see connected drives, engine activity and memory.',linkButton('How to enable live collection','settings','primary','live'),'live')}</section>`:
    `<div class="summary-strip"><span>${badge(state.paused?'Display paused':p.state||'Detection unavailable',canRate?'green':'')} <span style="margin-left:10px">${esc((p.engines||[]).map(e=>e.engine+' · PID '+e.pid).join(' / ')||'No engine identity available')}</span></span><span>${scope?'Fresh storage samples':'No fresh storage samples'} · ${d.sample_ms} ms collection · 1 s display</span></div><div class="metrics">${metric(p.unit==='chunks/s'?'Response writes':'Generation rate',unit(canRate?p.rate:null,p.unit||'tok/s'),esc(p.source||'Source unavailable'),'bolt',true)}${metric('Aggregate reads',unit(state.connected?d.cur_total:null,'GB/s'),d.cap_total?`${fmt(d.cap_total)} GB/s calibrated ceiling`:'Ceiling not calibrated','disk')}${metric('GPU activity',unit(fresh&&state.connected?sys.gpu:null,'% ',0),'System measurement · not engine attribution','live')}${metric('Available memory',unit(fresh&&state.connected?sys.ram_avail:null,'GiB',1),'Includes reclaimable memory','memory')}</div>`+
    `${state.monitorShowEngram&&!hasAttribution(d,devices)?`<div class="monitor-attribution-note"><strong>Weights + Engram overlay enabled</strong><span>${state.monitorShowSSD?'Waiting for per-request class telemetry. Grey bars remain device-wide until the engine supplies class intervals.':'Waiting for per-request class telemetry; SSD bars are hidden until selected.'}</span></div>`:''}${(state.monitorShowSSD||state.monitorShowEngram)?`<div class="live-drive-stack">${devices.map((x,i)=>{const color=devColor(x.id,i),rate=state.connected?d.cur?.[x.id]:null,cap=d.cap?.[x.id];return `<section class="device-card live-drive-card"><div class="live-drive-head"><div><h3><span class="legend-dot" style="background:${color}"></span>${esc(x.label||x.id)}</h3><p>${esc(x.connection)} · ${esc(x.device||'Not mounted')}</p></div><div><div class="metric-value">${unit(rate,'GB/s')}</div></div></div>${driveReadBars(d.read_windows?.[x.id],color,barScale,state.liveWindow,d,x.id)}<div class="device-facts"><span>Session peak ${d.traces?.[x.id]?.length?fmt(d.peaks?.[x.id])+' GB/s':'—'}</span><span>${finite(cap)?'Calibrated ceiling '+fmt(cap)+' GB/s':'Ceiling not calibrated'}</span></div></section>`;}).join('')}${state.monitorShowSSD&&state.monitorCombined!==false?combinedReadCard(d.read_windows?.TOTAL,barScale,state.liveWindow,devices.length,state.connected?d.cur_total:null,devices.reduce((s,x)=>s+(finite(d.cap?.[x.id])&&d.cap[x.id]>0?d.cap[x.id]:barScale.ceiling),0)):''}</div>`:empty('No chart layer selected','Enable SSDs or Monitor Engram above.')}`+
    (state.monitorAdvanced?advancedReadsView(d,readOpsHistory,{window:state.liveWindow,devices:devices.map((x,i)=>({...x,color:devColor(x.id,i)}))}):'')+
    '<details class="monitor-overview"><summary>Aggregate timeline · 120 seconds</summary>'+panel('Storage timeline','GB/s · aligned time windows · last 120 seconds',`<div class="panel-body">${livePlot(d)}<p class="chart-note">Aggregate uses simultaneous intervals across physical drives. It is not a sum of independent peaks.${state.paused?' Display frozen; the sampler continues running.':''}</p></div>`,badge(scope?'Receiving data':'Historical / stale',scope?'green':'amber'))+'</details>'+
    `<div class="two-col equal">${panel('Memory context','macOS unified memory · overlapping views are not added.',`<div class="panel-body"><div class="setting-line"><span>System memory used</span><strong>${fmt(fresh?sys.ram:null,1)} / ${fmt(d.ram_total,0)} GiB</strong></div><div class="memory-bar"><span style="width:${fresh&&d.ram_total?Math.min(100,(sys.ram||0)/d.ram_total*100):0}%"></span></div><div class="setting-line"><span>Engine RSS</span><strong>${fmt(fresh?sys.ram_engine:null,1)} GiB</strong></div><div class="setting-line"><span>Metal memory · system view</span><strong>${fmt(fresh?sys.gmem:null,1)} GiB</strong></div><div class="setting-line"><span>Current swap allocation</span><strong>${fmt(fresh?sys.swap:null,0)} MB</strong></div><p class="chart-note">Allocated swap alone does not show current swap traffic. Engine RSS and Metal memory can overlap.</p></div>`)}${panel('Telemetry health','Know when a measurement is unavailable.',`<div class="panel-body"><div class="setting-line"><span>Storage source</span><strong>${scope?'Fresh':'Unavailable or stale'}</strong></div><div class="setting-line"><span>System source</span><strong>${fresh?'Fresh':'Unavailable or stale'}</strong></div><div class="setting-line"><span>Engine detection</span><strong>${esc(d.health?.engine_detection||'Unknown')}</strong></div><div class="info-note" style="margin-top:15px">${p.unit==='chunks/s'?'The ds4 harness exposes response writes. This display labels them chunks/s; it cannot establish live tokenizer throughput.':'Process detection alone cannot establish token speed. The monitor waits for progress telemetry.'}</div></div>`)}</div>`);
  bindMonitorControls();
}
function renderSettings(){const s=state.settings;
  $('#main').innerHTML=heading('Make this workspace yours.','Connect your run history and choose how ARGODRIVE looks.')+
  `<div class="settings-grid"><div>${panel('Run folder','Read ds4 and deltafin benchmark artifacts in place.',`<form class="panel-body" id="source-form"><label class="field"><span>Folder containing your run folders</span><input type="text" id="runs-path" value="${esc(s.runs)}" placeholder="/path/to/arms" spellcheck="false" required><small>Expected layout: arms / run-folder / run.log. Optional .csv, .map, .sys, .md5 and trace files add evidence.</small></label><div class="button-group">${native?'<button class="button" type="button" data-action="choose-native-folder">Choose folder…</button>':''}<button class="button" type="button" id="validate-source">${icon('check')}Check folder</button><button class="button primary" type="submit">${icon('folder')}Use this folder</button></div><p class="form-status" id="source-status" role="status"></p><div class="info-note" style="margin-top:18px">ARGODRIVE reads the source files without moving or editing them. The folder selection is saved locally.${s.cli_override?' A --runs or --config launch argument takes priority again on the next restart.':''}</div></form>`)}${panel('Appearance','Choose a theme for this browser.',`<div class="panel-body"><label class="field"><span>Theme</span><select id="theme-select"><option value="system" ${theme==='system'?'selected':''}>Follow system</option><option value="light" ${theme==='light'?'selected':''}>Light</option><option value="dark" ${theme==='dark'?'selected':''}>Dark</option></select></label><div class="info-note">Keyboard: S opens Settings. Escape closes run details. Tab navigates every control.</div></div>`)}</div><div>${panel('Collection','Local monitoring has a measurable cost.',`<div class="panel-body"><div class="setting-line"><span>Current mode</span><strong>${s.mode==='reports'?'Reports only':'Live'}</strong></div><div class="setting-line"><span>Hardware sampling</span><strong>${s.mode==='reports'?'Off':s.sample_ms+' ms'}</strong></div><div class="setting-line"><span>Supported engines</span><strong>ds4 · deltafin</strong></div><div class="setting-line"><span>Version</span><strong>${esc(s.version)}</strong></div><details><summary>Enable live collection</summary>${native?'<p class="chart-note">Use the Monitor menu → Live hardware. The app will restart its own backend and start collection. Reports only stops collection.</p>':''}<p class="chart-note">From the ARGODRIVE folder, stop the current instance and start without --reports-only:</p><pre>./argodrive build\n./argodrive run --port ${location.port||'8130'} --runs /path/to/arms</pre><p class="chart-note">Drive discovery runs locally. Use --config for calibrated drive ceilings. This starts monitoring, not inference.</p></details><details><summary>Review results without sampling</summary><pre>./argodrive run --reports-only --runs /path/to/arms</pre><p class="chart-note">Pausing the live display does not stop collection. Use reports-only mode to avoid sampler overhead.</p></details></div>`)}${panel('What this preview includes','The measurement workspace for expert streaming.',`<div class="panel-body"><p class="info-note">Overview shows results. SSDs isolates drive activity. Streaming edits ds4 candidate settings and shows recorded read methods and arm files. Runs and Compare support repeatable experiments. Diagnostics holds read timing, expert activity, processes and raw samples.</p><p class="chart-note">ds4 settings can be reviewed, saved as local drafts and exported. Automated SSD tests, engine compatibility checks and validated tuning sessions are still being integrated. Exported candidates have not been benchmarked or applied.</p></div>`)}</div></div>`;
  $('#theme-select').onchange=e=>applyTheme(e.target.value);$('#source-form').onsubmit=e=>{e.preventDefault();saveSource(false);};$('#validate-source').onclick=()=>saveSource(true);
}
async function saveSource(validateOnly){const path=$('#runs-path').value.trim(),field=$('#source-status'),buttons=[...$('#source-form').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);field.textContent='Checking folder…';
  try{const result=await api('/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':state.settings.token},body:JSON.stringify({runs:path,validate_only:validateOnly})});field.textContent=`${result.logs} log files across ${result.blocks} folders. ${validateOnly?'Folder is readable.':'Data source updated.'}`;field.className='form-status positive';if(!validateOnly){state.selected.clear();state.baseline='';state.candidate='';state.trace='';state.expert='';await refresh();toast('Run folder updated. Source files were not changed.');}}
  catch(e){field.textContent=e.message;field.className='form-status negative';}finally{buttons.forEach(b=>b.disabled=false);}
}
function renderDiagnostics(){
  $('#main').innerHTML=heading('Follow the evidence.','Inspect read timing and expert activity without inferring speed gains from utilisation alone.')+`<div class="diagnostic-tabs" role="tablist" aria-label="Diagnostic views"><button role="tab" aria-selected="${state.diagnostic==='reads'}" class="${state.diagnostic==='reads'?'active':''}" data-diagnostic="reads">Read timing</button><button role="tab" aria-selected="${state.diagnostic==='experts'}" class="${state.diagnostic==='experts'?'active':''}" data-diagnostic="experts">Expert activity</button>${['processes','samples'].map(k=>`<button role="tab" aria-selected="${state.diagnostic===k}" class="${state.diagnostic===k?'active':''}" data-diagnostic="${k}">${k==='processes'?'Processes':'Raw samples'}</button>`).join('')}</div><div id="diagnostic-content"><div class="empty-state"><div class="loading-ring"></div><p>Finding recorded traces…</p></div></div>`;
  const version=state.loadVersion;if(['processes','samples'].includes(state.diagnostic))loadMachineDiagnostics(version);else loadDiagnostics(version);
}
async function loadMachineDiagnostics(version){
  const kind=state.diagnostic;
  if(state.data.mode==='reports'){$('#diagnostic-content').innerHTML=empty('Live collection is off','Enable Live Hardware in the Mac app to inspect this machine.',linkButton('Collection settings','settings'));return;}
  try{
    const d=await api(kind==='processes'?'/procs':'/peakreader');
    if(state.route!=='diagnostics'||state.diagnostic!==kind||version!==state.loadVersion)return;
    if(kind==='processes'){
      $('#diagnostic-content').innerHTML=panel('Processes by resident memory','Snapshot on request. Refresh to update.',d.available===false?empty('Process readings unavailable',d.error||'macOS did not provide process readings.'):`<div class="panel-body"><p class="info-note">Engine RSS: ${fmt(d.engine_rss,2)} GiB. Process RSS and system Metal memory can overlap; they are not added together.</p></div><div class="table-scroll"><table class="tight-table"><thead><tr><th>Process</th><th>PID</th><th>RSS GiB</th><th>CPU %</th></tr></thead><tbody>${(d.top||[]).map(r=>`<tr><td>${esc(r.name)}</td><td>${r.pid}</td><td>${fmt(r.gib,2)}</td><td>${fmt(r.cpu,1)}</td></tr>`).join('')}</tbody></table></div>`,button('Refresh','refresh-machine','small','refresh'));
    }else{
      const active=new Set((state.data.devices||[]).filter(x=>x.present).map(x=>x.id));
      $('#diagnostic-content').innerHTML=panel('Raw sampler intervals',`${d.tick_ms} ms configured sampling · last 10 seconds · no inference attribution`,`<div class="panel-body"><p class="info-note">These are completion-counter intervals. A single interval peak is not sustained bandwidth.</p></div><div class="table-scroll"><table class="tight-table"><thead><tr><th>Drive</th><th>Samples</th><th>Window max MB</th><th>Largest transfer MB</th><th>Its interval ms</th></tr></thead><tbody>${Object.entries(d.devs||{}).filter(([k])=>active.has(k)).map(([k,r])=>`<tr><td>${esc(k)}</td><td>${r.ticks.length}</td><td>${fmt(r.window_max_mb)}</td><td>${fmt(r.session_max_mb)}</td><td>${fmt(r.session_max_dt_ms)}</td></tr>`).join('')}</tbody></table></div>`,button('Refresh','refresh-machine','small','refresh')+button('Export raw samples','export-raw-samples','small','download'));
      state.rawSamples=d;
    }
  }catch(e){if(state.route==='diagnostics')$('#diagnostic-content').innerHTML=`<p class="data-error">${esc(e.message)}</p>`;}
}
async function loadDiagnostics(version){try{
  const kind=state.diagnostic,listing=await api(kind==='reads'?'/bylayer_list':'/traces');if(state.route!=='diagnostics'||version!==state.loadVersion||kind!==state.diagnostic)return;
  const list=listing.traces||[];
  if(!list.length){$('#diagnostic-content').innerHTML=panel(kind==='reads'?'Read timing':'Expert activity','',empty('No '+(kind==='reads'?'read traces':'expert profiles')+' in this folder',kind==='reads'?'Add a recorded .readtrace.csv beside its run log to inspect per-drive read timing.':'Add a .hotlist or .expert.json profile beside its run log to inspect expert reuse.',linkButton('Choose a data source','settings','','folder'),kind==='reads'?'disk':'diagnostics'));return;}
  if(kind==='reads'){if(!list.some(x=>x.path===state.trace))state.trace=list[0].path;}else if(!list.includes(state.expert))state.expert=list[0];
  const options=list.map(x=>kind==='reads'?`<option value="${esc(x.path)}" ${x.path===state.trace?'selected':''}>${esc(x.path.split('/').slice(-2).join('/'))} · ${x.mb} MB</option>`:`<option ${x===state.expert?'selected':''}>${esc(x)}</option>`).join('');
  $('#diagnostic-content').innerHTML=`<div class="trace-picker"><label for="trace-select">Recorded ${kind==='reads'?'read trace':'profile'}</label><select id="trace-select">${options}</select>${kind==='reads'?`<a id="trace-export" class="button" href="/bylayer.csv?trace=${encodeURIComponent(state.trace)}">${icon('download')}Export timing</a>`:''}</div><div id="trace-results"><div class="empty-state"><div class="loading-ring"></div><p>Reading the selected trace…</p></div></div>`;
  $('#trace-select').onchange=e=>{if(kind==='reads')state.trace=e.target.value;else state.expert=e.target.value;state.loadVersion++;loadDiagnostics(state.loadVersion);};
  if(kind==='reads')await loadReadTrace();else await loadExpertTrace();
  }catch(e){if($('#diagnostic-content'))$('#diagnostic-content').innerHTML=`<div class="info-note amber">${esc(e.message)}</div>`;}
}
async function loadReadTrace(barrier){const trace=state.trace,d=await api(`/bylayer?trace=${encodeURIComponent(trace)}${barrier===undefined?'':'&barrier='+barrier}`);if(state.route!=='diagnostics'||state.diagnostic!=='reads'||trace!==state.trace)return;
  const rows=d.summary||[];
  $('#trace-results').innerHTML=`<div class="metrics">${metric('Recorded reads',val(d.records,0),'Across the whole trace','disk',true)}${metric('Layer passes',val(d.barriers?.length,0),'Recorded barrier groups','runs')}${metric('Read span · p50',unit(d.span_p50,'ms'),'Span of recorded reads in each pass','clock')}${metric('Read volume',unit(rows.reduce((n,r)=>n+(r.bytes||0),0)/1e9,'GB'),esc(d.byte_source),'disk')}</div>`+
    panel('Which drive lands last?','Read-tail attribution across recorded passes.',`<div class="table-scroll"><table><thead><tr><th>Device</th><th class="cell-num">Reads</th><th class="cell-num">Volume · GB</th><th class="cell-num">Last to finish</th><th class="cell-num">Gap p50 · ms</th><th class="cell-num">Gap p90 · ms</th><th class="cell-num">Mean concurrent reads</th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td><span class="legend-dot" style="background:${devColor(r.dev,i)}"></span>${esc(r.dev)}</td><td class="cell-num">${val(r.reads,0)}</td><td class="cell-num">${val(r.gb,1)}</td><td class="cell-num">${val(r.last_pct,1)}%</td><td class="cell-num">${val(r.wait_p50)}</td><td class="cell-num">${val(r.wait_p90)}</td><td class="cell-num">${val(r.concurrency)}</td></tr>`).join('')}</tbody></table></div><div class="panel-body" style="padding-top:16px"><div class="info-note">${esc(d.timing_note)} Mean concurrency uses ${esc(d.concurrency_basis)}.</div></div>`)+
    `<div class="two-col equal">${panel('Inspect a layer pass','Largest last-landing gaps first · click a pass.',`<div class="barrier-list"><table class="tight-table"><thead><tr><th>Pass</th><th>Layer</th><th>Last drive</th><th class="cell-num">Gap · ms</th></tr></thead><tbody>${[...(d.barriers||[])].sort((a,b)=>b.wait_ms-a.wait_ms).slice(0,150).map(r=>`<tr><td><button class="barrier-button" data-barrier="${r.b}">${r.b}</button></td><td>${r.layer}</td><td>${esc(r.last)}</td><td class="cell-num">${val(r.wait_ms)}</td></tr>`).join('')}</tbody></table></div>`)}${panel('Read landing timeline',d.gantt?`Pass ${d.gantt.b} · layer ${d.gantt.layer} · offsets in milliseconds`:'No pass selected',`<div class="panel-body">${gantt(d.gantt)}<p class="chart-note">Bars show read start → completion. They do not measure GPU execution or GPU idle time.</p></div>`)}</div>`;
}
function gantt(g){if(!g?.reads?.length)return '<p class="muted">No reads recorded for this pass.</p>';const rows=g.reads,max=Math.max(...rows.map(r=>r.end),.01);return `<div class="gantt">${rows.slice(0,160).map(r=>`<div class="gantt-row"><span>${esc(r.dev)} · ${r.expert}</span><div class="gantt-track"><span class="gantt-bar" style="left:${Math.max(0,r.start/max*100)}%;width:${Math.max(.1,(r.end-r.start)/max*100)}%;background:${devColor(r.dev)}" title="${esc(r.dev)} expert ${r.expert}: ${r.start}–${r.end} ms"></span></div></div>`).join('')}</div><div class="table-footer" style="padding:8px 0"><span>0 ms${rows.length>160?' · first 160 of '+rows.length+' reads':''}</span><span>${fmt(max)} ms</span></div>`;}
async function loadExpertTrace(){const trace=state.expert,d=await api('/experts?trace='+encodeURIComponent(trace));if(state.route!=='diagnostics'||state.diagnostic!=='experts'||trace!==state.expert)return;
  const cells=d.cells||[],top=[...cells].sort((a,b)=>b[2]-a[2]).slice(0,20);
  $('#trace-results').innerHTML=`<div class="metrics">${metric('Expert routes',val(d.total_routes,0),'Profile counts','diagnostics',true)}${metric('Experts used',val(d.used,0),'Distinct layer + expert pairs','runs')}${metric('Experts used again',val(d.reused,0),'More than one route in this profile','refresh')}${metric('Layers represented',val(d.layers,0),'Reported profile dimensions','runs')}</div>`+
    panel('Expert activity map','Rows are layers. Columns are experts. Intensity uses log-scaled route counts.',`<div class="panel-body"><canvas id="expert-heatmap" class="trace-heatmap" aria-label="Expert route heatmap. Highest counts are also listed in the table below."></canvas><div class="heat-scale"><span>Fewer routes</span><i></i><span>More routes</span></div><div class="info-note" style="margin-top:18px">Repeated expert use is not a measured RAM cache hit. The profile does not establish which reads hit memory or the benefit of a cache policy.</div></div>`)+
    panel('Most-used experts','Top 20 layer + expert pairs in this profile.',`<div class="table-scroll"><table class="tight-table"><thead><tr><th>Layer</th><th>Expert</th><th class="cell-num">Routes</th></tr></thead><tbody>${top.map(c=>`<tr><td>${c[0]}</td><td>${c[1]}</td><td class="cell-num">${fmt(c[2],0)}</td></tr>`).join('')}</tbody></table></div>`);
  const canvas=$('#expert-heatmap'),width=Math.max(1,d.experts||1),height=Math.max(1,d.layers||1,cells.reduce((n,c)=>Math.max(n,c[0]+1),1));canvas.width=width*2;canvas.height=height*4;const ctx=canvas.getContext('2d');ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--surface-2');ctx.fillRect(0,0,canvas.width,canvas.height);const max=cells.reduce((n,c)=>Math.max(n,c[2]),1);for(const[l,e,n]of cells){ctx.fillStyle=`rgba(116,99,230,${.15+.85*Math.log1p(n)/Math.log1p(max)})`;ctx.fillRect(e*2,l*4,2,3);}
}
async function exportComparison(){const a=findRun(state.baseline),b=findRun(state.candidate);if(!a||!b){toast('Choose two runs first.');return;}const c=compare(a,b),pairs=[['Steady decode',a.tok_s_steady,b.tok_s_steady,'tok/s',false],['First response',a.first_token_s,b.first_token_s,'s',true],['Inclusive throughput',a.tok_s,b.tok_s,'tok/s',false]];
  const report=`# ARGODRIVE comparison\n\nBaseline: ${a.arm} (${a.block})\nCandidate: ${b.arm} (${b.block})\n\n${c.reason}. One pair; no confidence interval.\n\n| Metric | Baseline | Candidate | Improvement |\n| --- | ---: | ---: | ---: |\n${pairs.map(([name,x,y,u,lower])=>`| ${name} (${u}) | ${fmt(x,4)} | ${fmt(y,4)} | ${c.matched&&gain(x,y,lower)!==null?fmt(gain(x,y,lower),1)+'%':'Not established'} |`).join('\n')}\n\nOutput fingerprint: ${c.output}. Matching hashes do not establish answer quality.\n\nWorkload checks:\n${c.checks.map(x=>'- '+x.label+': '+x.state).join('\n')}\n\nMeasurements come from saved harness logs. First response includes setup and prefill. ds4 generated counts use response chunks. Review configuration differences and repeat in reversed order before promoting.\n`;
  download('argodrive-comparison.md',report,'text/markdown');toast('Comparison exported.');
}
document.addEventListener('click',async e=>{
  const target=e.target.closest('button');if(!target)return;
  if(target.dataset.streaming){state.streamingRun=target.dataset.streaming;$('#run-dialog').close();location.hash='streaming';return;}
  if(target.dataset.detail){showDetail(target.dataset.detail);return;}
  if(target.dataset.baseline||target.dataset.candidate){if(target.dataset.baseline)state.baseline=target.dataset.baseline;else state.candidate=target.dataset.candidate;$('#run-dialog').close();location.hash='compare';if(state.route==='compare')renderCompare();return;}
  if(target.dataset.diagnostic){state.diagnostic=target.dataset.diagnostic;state.loadVersion++;renderDiagnostics();return;}
  if(target.dataset.barrier!==undefined){try{await loadReadTrace(Number(target.dataset.barrier));}catch(e){toast(e.message);}return;}
  switch(target.dataset.action){
    case 'refresh':refresh();break;
    case 'refresh-cluster':state.loadVersion++;renderCluster();break;
    case 'export-cluster':if(state.cluster)download('argodrive-cluster-evidence.json',JSON.stringify(state.cluster,null,2),'application/json');break;
    case 'choose-native-folder':native?.postMessage({action:'chooseFolder'});break;
    case 'close-detail':$('#run-dialog').close();break;
    case 'clear-filters':state.filters={};state.page=0;renderRuns();break;
    case 'clear-selection':state.selected.clear();updateRunResults();break;
    case 'compare-selected':[state.baseline,state.candidate]=[...state.selected];location.hash='compare';break;
    case 'prev-page':state.page--;updateRunResults();break;
    case 'next-page':state.page++;updateRunResults();break;
    case 'swap-comparison':[state.baseline,state.candidate]=[state.candidate,state.baseline];renderCompare();break;
    case 'pause-live':state.paused=!state.paused;(state.route==='ssds'?renderSSDs:renderLive)();break;
    case 'export-ssd':exportSSD();break;
    case 'rescan-topology':loadTopology(true);break;
    case 'export-topology':exportTopology();break;
    case 'refresh-machine':state.loadVersion++;loadMachineDiagnostics(state.loadVersion);break;
    case 'export-raw-samples':download('argodrive-raw-samples.json',JSON.stringify(state.rawSamples,null,2),'application/json');break;
    case 'show-connected':state.ssdFilter='connected';renderSSDs();break;
    case 'export-arm-files':exportArmFiles();break;
    case 'export-comparison':exportComparison();break;
  }
});
document.addEventListener('change',e=>{if(e.target.dataset.select){const id=e.target.dataset.select;if(e.target.checked){if(state.selected.size===2){e.target.checked=false;toast('Select up to two runs. Clear one to choose another.');return;}state.selected.add(id);}else state.selected.delete(id);updateRunResults();}});
document.addEventListener('keydown',e=>{if(e.key.toLowerCase()==='s'&&!e.metaKey&&!e.ctrlKey&&!e.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!$('#run-dialog').open){e.preventDefault();location.hash='settings';}});
$('#run-dialog').addEventListener('click',e=>{if(e.target===$('#run-dialog')){const box=e.target.getBoundingClientRect();if(e.clientX<box.left||e.clientX>box.right||e.clientY<box.top||e.clientY>box.bottom)e.target.close();}});
let pollBusy=false;
function renderActiveLive(){
  if(state.route==='monitor'&&state.monitorSource!=='hardware')return;
  if(state.route==='engram'){renderEngram();return;}
  if(state.route==='topology'){updateTopology();return;}
  // Keep an open native select stable while fresh measurements arrive.
  if(state.route==='monitor' && document.activeElement?.matches('#monitor-show-ssd,#monitor-show-engram,#live-window,#monitor-source'))return;
  if(state.route==='ssds' && (document.activeElement?.matches('.ssd-toolbar select')||document.activeElement?.closest('.spotlight-panel')||$('#run-dialog').open))return;
  (state.route==='ssds'?renderSSDs:renderLive)();
}
setInterval(async()=>{if(document.hidden||(!['monitor','engram','ssds','topology'].includes(state.route)||(state.route==='monitor'&&state.monitorSource!=='hardware'))||state.paused||state.data?.mode==='reports'||pollBusy)return;pollBusy=true;try{state.data=await api('/data');captureReadOps(state.data);state.connected=true;notice();renderActiveLive();updateChrome();}catch(e){state.connected=false;notice('Live connection lost. Previously collected charts are retained; current values are unavailable.');renderActiveLive();updateChrome();}finally{pollBusy=false;}},1000);
window.addEventListener('argodrive-folder',async e=>{if(!native||typeof e.detail!=='string')return;state.route='settings';location.hash='settings';renderSettings();$('#runs-path').value=e.detail;await saveSource(false);});
navigate();refresh();

document.addEventListener('click',e=>{const n=e.target.closest('[data-topology-node]');if(n){state.topologySelected=n.dataset.topologyNode;updateTopology();}});
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('.topo-node')){e.preventDefault();state.topologySelected=e.target.dataset.topologyNode;updateTopology();}});
