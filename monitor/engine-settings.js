import {escapeHTML as esc} from './app-model.js';

// Capability reference, not detection of the user's executable. Fork-only
// controls are deliberately absent from the conservative upstream profile.
export const DS4_REFERENCE={base:'6289c51',backend:'Metal',reviewed:'2026-09-12',
  fork_source_sha256:'af57b29f7e62b8b2cbb78c2312abbe1ace68b4c55307ca578ae944e9b0bcb539',
  scope:'Source-reviewed capability subset; installed binary and model compatibility unverified'};
export const DS41_REFERENCE={base:'bd66c402070042bf0a79ad6ece8242de4c93680c',backend:'Metal',reviewed:'2026-09-12',scope:'V4.1 upstream and experimental Argodrive fork; installed executable and user settings require verification'};
const ds41Keys=['DS4_ARGODRIVE_REPLICAS','DS4_ARGODRIVE_PRIMARY_WEIGHT','DS4_ARGODRIVE_PRIMARY_NOCACHE','DS4_ARGODRIVE_QUEUE_LAYERS','DS4_ARGODRIVE_EARLY_EXPERTS','DS4_ARGODRIVE_RESIDENT_GATE','DS4_ARGODRIVE_ENGRAM_READERS','DS4_ARGODRIVE_PHASES','DS4_ARGODRIVE_DECODE_CACHE_PCT'];
const key={threads:'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS',readAhead:'DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD',
  replicas:'DS4_MODEL_REPLICAS',weight:'DS4_MODEL_PRIMARY_WEIGHT',split:'DS4_MODEL_REPLICA_SPLIT',
  pieces:'DS4_MODEL_REPLICA_PIECES_FD',globalPieces:'DS4_MODEL_REPLICA_PIECES',inflight:'DS4_MODEL_REPLICA_INFLIGHT',
  nocache:'DS4_MODEL_FD_NOCACHE',lru:'DS4_METAL_STREAM_EXPERT_EVICT_LRU',prefetch:'DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH',
  prefill:'DS4_METAL_DISABLE_GLM_STREAMING_PREFILL_FULL_LAYER'};
const storageKey='argodrive-ds4-candidate-v1';
export const newEngineDraft=()=>({schema:1,engine:'ds4',backend:'metal',build:'standard',buildId:'',modelFamily:'other',
  objective:'decode',threads:'',readAhead:'default',cache:'',decodeCachePct:'',cold:false,method:'single',nocache:'default',lru:'default',
  prefetch:'',prefill:'default',sources:[{path:'',weight:'1',pieces:'1',inflight:'0'}]});
function integer(value,min,max,label){
  if(!/^\d+$/.test(String(value))||Number(value)<min||Number(value)>max)throw Error(`${label}: enter a whole number from ${min} to ${max}.`);
  return Number(value);
}
function choice(value,allowed,label){if(!allowed.includes(value))throw Error(`Choose a supported ${label}.`);return value;}
function text(value,label){if(typeof value!=='string'||value.length>4096||/[\x00-\x1f\x7f]/.test(value))throw Error(`${label} contains unsupported characters or is too long.`);return value.trim();}
export function checkedDraft(d){
  const baseline=newEngineDraft(),fields=Object.keys(baseline);
  if(!d||typeof d!=='object'||Object.keys(d).some(k=>!fields.includes(k)&&k!=='decodeCachePct')||d.schema!==1||d.engine!=='ds4'||d.backend!=='metal'||typeof d.cold!=='boolean')throw Error('Unsupported engine draft schema.');
  // Drafts saved before the decode-cache control are upgraded in memory.
  if(!Object.hasOwn(d,'decodeCachePct'))d.decodeCachePct='';
  for(const name of fields.filter(k=>typeof baseline[k]==='string'))text(d[name],name);
  for(const [name,allowed] of Object.entries({build:['standard','argonaut'],modelFamily:['glm','deepseek','deepseek41','other'],objective:['decode','prefill','balanced'],method:['single','split','hashed'],readAhead:['default','off'],nocache:['default','on','off'],lru:['default','on'],prefill:['default','selected']}))choice(d[name],allowed,name);
  if(d.decodeCachePct!==''&&(!/^\d+$/.test(String(d.decodeCachePct))||Number(d.decodeCachePct)<1||Number(d.decodeCachePct)>100))throw Error('Decode cache reserve must be blank or a percentage from 1 to 100.');
  if(!Array.isArray(d.sources)||d.sources.length<1||d.sources.length>8)throw Error('A draft supports 1–8 sources.');
  for(const s of d.sources){if(!s||Object.keys(s).sort().join()!=='inflight,path,pieces,weight')throw Error('Invalid source draft.');for(const [k,v] of Object.entries(s))text(v,k);}
  return d;
}
export function engineProfile(d){
  d=checkedDraft(d);
  if(d?.schema!==1||d.engine!=='ds4'||d.backend!=='metal')throw Error('This editor supports ds4 on Metal.');
  choice(d.build,['standard','argonaut'],'engine build');choice(d.modelFamily,['glm','deepseek','deepseek41','other'],'model family');
  choice(d.objective,['decode','prefill','balanced'],'test objective');choice(d.method,['single','split','hashed'],'read method');
  choice(d.readAhead,['default','off'],'read-ahead policy');choice(d.nocache,['default','on','off'],'page-cache policy');
  choice(d.lru,['default','on'],'eviction policy');choice(d.prefill,['default','selected'],'GLM prefill policy');
  if(typeof d.cold!=='boolean')throw Error('Cold start must be on or off.');
  const fork=d.build==='argonaut',glm=d.modelFamily==='glm',buildId=text(d.buildId,'Build identity');
  if(!Array.isArray(d.sources)||d.sources.length<1||d.sources.length>8)throw Error('Select 1–8 model source files.');
  if(d.method==='single'&&d.sources.length!==1)throw Error('Single-source mode requires one source. Remove replicas or choose a replica method.');
  if(d.method!=='single'&&(!fork||d.sources.length<2))throw Error('Replica methods require the Argonaut ds4 fork and at least two complete model files.');
  if(!fork&&(d.nocache!=='default'||d.lru!=='default'||d.sources.length!==1))throw Error('These controls require the Argonaut ds4 fork profile.');
  if((!fork||!glm)&&(d.prefetch!==''||d.prefill!=='default'))throw Error('GLM controls require the GLM family and Argonaut ds4 fork.');
  const ds41=d.modelFamily==='deepseek41';
  if(ds41&&fork&&(d.sources.length>3||d.method==='hashed'||d.lru!=='default'))throw Error('The V4.1 fork supports at most three sources, single or split reads, and the engine default eviction policy.');
  const sources=d.sources.map((s,i)=>{
    const path=text(s.path,`Source ${i+1}`);
    if(!path.startsWith('/'))throw Error(`Source ${i+1}: enter the absolute path to the model file.`);
    if(/[,*]/.test(path))throw Error(`Source ${i+1}: ds4's replica syntax cannot represent commas or asterisks in paths.`);
    const weight=integer(s.weight,1,64,'Source weight'),pieces=integer(s.pieces,1,16,'Pieces per source'),inflight=integer(s.inflight,0,64,'In-flight limit');
    if(!fork&&(weight!==1||pieces!==1||inflight!==0))throw Error('Source allocation controls require the Argonaut ds4 fork profile.');
    if(ds41&&fork&&(pieces!==1||inflight!==0))throw Error('V4.1 uses one piece per source and its bounded engine pool; per-source piece and in-flight controls are not implemented.');
    return {path,weight,pieces,inflight,physical_device_verified:false,replica_integrity_verified:false};
  });
  if(new Set(sources.map(s=>s.path.replace(/\/+$/,''))).size!==sources.length)throw Error('Use distinct source paths. Physical-device deduplication still needs verification.');
  if(sources.reduce((sum,s)=>sum+s.weight,0)>64)throw Error('Use a total source weight of 64 or less in this editor.');
  if(d.method!=='split'&&sources.some(s=>s.inflight!==0)||d.method==='hashed'&&sources.some(s=>s.pieces!==1))throw Error('Per-source pieces and in-flight limits are only offered for split reads; use 0 (uncapped) outside split mode, and 1 piece for offset-hashed reads.');
  const environment={},argv=['--metal','--ssd-streaming','-m',sources[0].path];
  if(d.threads!=='')environment[key.threads]=String(integer(d.threads,1,fork&&!ds41?64:18,'Reader threads'));
  // This flag is tested by presence. Setting it to "0" would still disable read-ahead.
  if(d.readAhead==='off')environment[key.readAhead]='1';
  const cache=text(d.cache,'Cache target');
  if(cache){
    if(!/^(?:[1-9]\d{0,8}|\d{1,6}(?:\.\d{1,3})?GB)$/.test(cache)||parseFloat(cache)<=0)throw Error('Cache target must be a positive expert count or a size such as 60GB. Leave blank for automatic sizing.');
    argv.push('--ssd-streaming-cache-experts',cache);
  }
  if(ds41)argv.push('--ctx','4096','--think-level','0','--power','100');
  if(d.cold)argv.push('--ssd-streaming-cold');
  if(fork&&ds41){
    Object.assign(environment,{'DS4_ARGODRIVE_QUEUE_LAYERS':'1','DS4_ARGODRIVE_EARLY_EXPERTS':'1',
      'DS4_ARGODRIVE_RESIDENT_GATE':'1','DS4_ARGODRIVE_ENGRAM_READERS':'8','DS4_ARGODRIVE_PHASES':'1'});
    if(d.decodeCachePct!=='')environment.DS4_ARGODRIVE_DECODE_CACHE_PCT=String(integer(d.decodeCachePct,1,100,'Decode cache reserve'));
    if(d.method==='split'){
      environment.DS4_ARGODRIVE_REPLICAS=sources.slice(1).map(s=>`${s.path}*${s.weight}`).join(',');
      environment.DS4_ARGODRIVE_PRIMARY_WEIGHT=String(sources[0].weight);
    }
    if(d.nocache!=='default')environment.DS4_ARGODRIVE_PRIMARY_NOCACHE=d.nocache==='on'?'1':'0';
  }else if(fork){
    environment[key.globalPieces]=d.method==='single'?String(sources[0].pieces):'1';
    environment[key.pieces]=sources.map(s=>s.pieces).join(',');
    if(d.method!=='single'){
      environment[key.replicas]=sources.slice(1).map(s=>`${s.path}*${s.weight}`).join(',');
      environment[key.weight]=String(sources[0].weight);environment[key.split]=d.method==='split'?'1':'0';
      environment[key.inflight]=sources.map(s=>s.inflight).join(',');
    }
    if(d.nocache!=='default')environment[key.nocache]=d.nocache==='on'?'1':'0';
    if(d.lru==='on')environment[key.lru]='1';
    if(glm&&d.prefetch!==''){
      const count=integer(d.prefetch,0,8,'GLM lookahead experts');
      if(count>0&&(d.method==='hashed'||d.method==='single'&&sources[0].pieces===1))throw Error('The audited lookahead path needs split reads or multiple primary pieces. A one-piece single source does not activate this shared planner.');
      environment[key.prefetch]=String(count);
    }
    if(glm&&d.prefill==='selected')environment[key.prefill]='1';
  }
  return {schema:1,kind:'argodrive-engine-candidate',status:'unvalidated',engine:{name:'ds4',backend:'metal',build_profile:d.build,build_id:buildId||null,installed_binary_verified:false,reference:ds41?DS41_REFERENCE:DS4_REFERENCE},
    model:{family:d.modelFamily,path:sources[0].path,compatibility_verified:false,...(ds41?{engram_disk_only:true,engram_row_bytes:264,speculation_supported:false,local_qualification:false}:{})},objective:d.objective,method:d.method,
    argv,environment,unset_environment:[...Object.values(key),...ds41Keys].filter(k=>!(k in environment)),sources,
    validation:{arms:[],speed_gain_percent:null},
    notes:['Candidate settings; no engine was launched or reconfigured.','CLI arguments must be passed by the launcher; sourcing the environment file does not apply CLI arguments.',
      'Only listed controls are managed. Other launcher options and environment variables are unchanged; use a clean matched harness for qualification.',
      'Source paths are unverified. Resolve physical SSDs, check identical replicas and calibrate shared uplinks before testing.',
      'Cold start skips preload; it does not disable the runtime RAM cache. GB cache targets include prefill reserve and may be reduced by ds4.',
      'Decode cache reserve transfers unused prefill headroom into decode expert slots without increasing total memory allowance. Start with 100% only after a matched A/B run and swap guard.']};
}
export const shellQuote=value=>"'"+String(value).replaceAll("'","'\\''")+"'";
export function engineEnvironment(profile){
  return ['# ARGODRIVE ds4 candidate — unvalidated; no automatic launch',
    '# CLI arguments (pass through your ds4 launcher):', '# '+profile.argv.map(shellQuote).join(' '),
    '# Only the controls below are managed. Other settings remain unchanged.',
    ...profile.unset_environment.map(k=>'unset '+k),
    ...Object.entries(profile.environment).map(([k,v])=>'export '+k+'='+shellQuote(v)), ''].join('\n');
}
const selected=(a,b)=>a===b?'selected':'';
const options=(values,current)=>values.map(([value,label])=>`<option value="${value}" ${selected(value,current)}>${label}</option>`).join('');
const field=(label,body,note='')=>`<label class="field"><span>${label}</span>${body}${note?`<small>${note}</small>`:''}</label>`;
const select=(name,values,current)=>`<select name="${name}">${options(values,current)}</select>`;
const input=(name,value,placeholder='',type='text')=>`<input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" spellcheck="false">`;
export function engineSettingsView(d){
  const fork=d.build==='argonaut',glm=d.modelFamily==='glm',ds41=d.modelFamily==='deepseek41';
  return `<section class="panel engine-editor"><div class="panel-head"><div><h2>ds4 engine settings</h2><p>The engine moves expert bytes. The model determines the read pattern and which extra controls apply.</p></div><span class="badge amber">Candidate · not applied</span></div>
  <form class="panel-body" id="ds4-settings-form">
  <div class="engine-identity"><div><span class="eyebrow">ENGINE</span><strong>ds4 <small>Metal</small></strong></div><div><span class="eyebrow">MODEL FAMILY</span><strong>${{glm:'GLM',deepseek:'DeepSeek V4 / other',deepseek41:'DeepSeek V4.1 Flash',other:'Other / unspecified'}[d.modelFamily]}</strong></div><div><span class="eyebrow">VALIDATION</span><strong>Needs benchmark</strong></div></div>
  <div class="engine-fields">
  ${field('Engine build profile',select('build',[['standard','ds4 core controls'],['argonaut','Argonaut ds4 fork · replica streaming']],d.build),'Select the capabilities your build includes. The installed executable is not automatically verified.')}
  ${field('Model family',select('modelFamily',[['other','Other / unspecified'],['glm','GLM'],['deepseek','DeepSeek V4 / other'],['deepseek41','DeepSeek V4.1 Flash · experimental']],d.modelFamily),'Recommendations and benchmark results belong to one model and workload.')}
  ${field('Build identity',input('buildId',d.buildId,'Commit / binary hash, if known'),'Record your exact build for a reproducible comparison.')}
  ${field('Test objective',select('objective',[['decode','Decode token speed'],['prefill','Prefill / first response'],['balanced','Both phases']],d.objective),'Higher SSD GB/s alone is not a token-speed improvement.')}
  </div>
  ${ds41?`<p class="info-note">V4.1: 4K context, thinking off, power 100, automatic cache unless specified. ${fork?'This experimental fork profile enables layer queueing, exact early expert loading, resident gate/up work, eight primary-SSD Engram readers and phase markers. It uses DS4_ARGODRIVE flags, not the GLM fork’s DS4_MODEL flags. Verify the exact build before use.':'Choose core controls for upstream ds4, or the Argonaut fork profile for the separate DeepSeek implementation.'}</p>`:''}
  <h3 class="engine-section-title">SSD streaming</h3>
  <div class="engine-fields">
  ${field('Read method',select('method',fork?[['single','Single source'],['split','Weighted replica split reads'],...(!ds41?[['hashed','Offset-hashed replica reads']]:[])]:[['single','Single source']],d.method),'Replica reads require complete, identical model copies. They are application-level reads, not RAID0.')}
  ${field('Reader threads',input('threads',d.threads,'Engine default'),`1–${fork&&!ds41?64:18} workers in this source-reviewed profile. More workers can increase contention.`)}
  ${field('OS expert read-ahead',select('readAhead',[['default','Engine default'],['off','Disabled']],d.readAhead),'This is OS read-ahead advice, separate from model router lookahead.')}
  ${fork?field(ds41?'Primary expert page-cache policy':'Expert file page-cache policy',select('nocache',[['default','Engine default'],['on','F_NOCACHE enabled'],['off','F_NOCACHE disabled']],d.nocache),ds41?'Applies to an independent primary expert descriptor; replicas always request F_NOCACHE. Engram descriptor unchanged.':'A caching policy request; it is not proof of physical NAND read volume.'):''}
  </div>
  <div class="engine-source-heading"><h3>Model source files</h3>${fork?'<button type="button" class="button small" data-engine-action="add-source">Add replica source</button>':''}</div>
  <p class="chart-note">Order is primary first, then replica descriptors. Paths do not establish separate physical SSDs. Drives on the same hub share an uplink; test that branch together.</p>
  <div class="table-scroll"><table class="tight-table engine-sources"><thead><tr><th>Source file</th>${fork?'<th>Weight</th><th>Pieces</th><th>In flight</th><th></th>':''}</tr></thead><tbody>${d.sources.map((s,i)=>`<tr><td><label class="field"><span>${i?'Replica '+i:'Primary model'}</span><input name="source-${i}-path" aria-label="${i?'Replica '+i:'Primary model'} path" value="${esc(s.path)}" placeholder="/path/to/model.gguf" spellcheck="false"></label></td>${fork?`<td><input name="source-${i}-weight" aria-label="Source ${i+1} weight" type="number" min="1" max="64" value="${esc(s.weight)}"></td><td><input name="source-${i}-pieces" aria-label="Source ${i+1} pieces" type="number" min="1" max="16" ${ds41?'disabled':''} value="${esc(s.pieces)}"></td><td><input name="source-${i}-inflight" aria-label="Source ${i+1} in-flight limit" type="number" min="0" max="64" ${ds41?'disabled':''} value="${esc(s.inflight)}"></td><td>${i?`<button type="button" class="button small" data-engine-action="remove-source" data-index="${i}" aria-label="Remove replica ${i}">Remove</button>`:''}</td>`:''}</tr>`).join('')}</tbody></table></div>
  ${fork?`<p class="chart-note">${ds41?'Up to three sources. Start with weights 2:1:1. Keep Pieces at 1 and In flight at 0; the V4.1 fork does not implement those per-source controls.':'Positive weights total at most 64. Pieces: 1–16 per source. In flight: 0 means uncapped; limits are offered for split reads.'} These settings need matched measurement on your hardware.</p>`:''}
  <h3 class="engine-section-title">RAM expert cache</h3>
  <div class="engine-fields">
  ${field('Cache target',input('cache',d.cache,'Automatic · or 60GB / 2846'),'ds4 syntax: a positive expert count or NGB. GB targets include prefill reserve and may be reduced to fit memory.')}
  ${fork&&ds41?field('Decode cache reserve',select('decodeCachePct',[['','Automatic / baseline'],['100','Use 100% of unused prefill reserve']],d.decodeCachePct),'Argonaut fork only. Transfers existing prefill headroom into decode expert slots; total memory allowance is unchanged. Qualify with an A/B run and swap guard.') :''}
  ${fork&&!ds41?field('Eviction policy',select('lru',[['default','Engine default'],['on','Pure LRU']],d.lru),'A fork control; compare cache hits and token speed before promotion.'):''}
  <label class="engine-check"><input name="cold" type="checkbox" ${d.cold?'checked':''}><span>Cold start<small>Skip popularity preload. The runtime RAM expert cache remains enabled.</small></span></label>
  </div>
  ${fork&&glm?`<h3 class="engine-section-title">GLM extensions <span class="badge">Model-specific</span></h3><div class="engine-fields">${field('Router lookahead experts',input('prefetch',d.prefetch,'Engine default'),'0–8 predicted experts from layer +1. Check installed, useful and late prefetch counts in the run.')}${field('Prefill path',select('prefill',[['default','Engine default'],['selected','Selected experts']],d.prefill),'This GLM fork control does not apply to every model supported by ds4.')}</div>`:'<p class="info-note">GLM router lookahead and prefill controls appear only for the GLM family with the Argonaut ds4 fork.</p>'}
  <div class="engine-footer"><div class="button-group"><button class="button primary" type="submit">Review candidate</button><button class="button" type="button" data-engine-action="save">Save draft</button></div><p>No run is started. Settings are not applied to an active engine.</p></div>
  <p class="form-status" id="engine-settings-status" role="status"></p>
  </form></section><div id="engine-candidate-review"></div>`;
}
let draft, reviewed;
function initialDraft(saved){
  try{if(saved)return checkedDraft(saved);}catch{}
  try{const d=JSON.parse(localStorage.getItem(storageKey));if(d?.schema===1)return checkedDraft(d);}catch{}
  return newEngineDraft();
}
export function mountEngineSettings(container,host={}){
  draft??=initialDraft(host.savedDraft);reviewed=null;container.innerHTML=engineSettingsView(draft);
  const form=container.querySelector('form'),status=container.querySelector('#engine-settings-status');
  function read(){
    const data=new FormData(form);
    for(const name of ['build','buildId','modelFamily','objective','threads','readAhead','cache','decodeCachePct','method','nocache','lru','prefetch','prefill'])if(data.has(name))draft[name]=String(data.get(name));
    draft.cold=data.has('cold');
    draft.sources=draft.sources.map((s,i)=>Object.fromEntries(Object.keys(s).map(k=>[k,data.has(`source-${i}-${k}`)?String(data.get(`source-${i}-${k}`)):s[k]])));
    reviewed=null;container.querySelector('#engine-candidate-review').innerHTML='';status.textContent='';
  }
  function message(value,error=false){status.textContent=value;status.className='form-status '+(error?'negative':'positive');}
  function review(){
    read();
    try{
      reviewed=engineProfile(draft);
      container.querySelector('#engine-candidate-review').innerHTML=`<section class="panel"><div class="panel-head"><div><h2>Candidate settings</h2><p>ds4 · ${esc(reviewed.engine.build_profile)} · ${esc(reviewed.model.family)} · ${esc(reviewed.objective)}</p></div><span class="badge amber">Unvalidated</span></div><div class="panel-body"><div class="button-group"><button type="button" class="button primary" data-engine-action="export-json">Export profile JSON</button><button type="button" class="button" data-engine-action="export-env">Export environment</button></div><p class="chart-note">The environment file sets only environment controls. Pass the CLI arguments below through your existing launcher. Replica identity, shared uplinks and speed still require validation.</p><pre class="engine-candidate-code">${esc(engineEnvironment(reviewed))}</pre><p class="chart-note">Next: verify the build and source files, run a matched control/candidate comparison, then use Recorded arms and Compare to review the effective settings and output.</p></div></section>`;
      message('Candidate syntax checked. Engine and hardware compatibility remain unverified.');
    }catch(e){message(e.message,true);}
  }
  form.addEventListener('input',read);
  form.addEventListener('change',e=>{
    read();
    if(['build','modelFamily'].includes(e.target.name)){
      draft.prefetch='';draft.prefill='default';
      if(draft.modelFamily==='deepseek41'&&e.target.name==='modelFamily')draft.build='standard';
      if(draft.modelFamily==='deepseek41'&&draft.build==='argonaut'){
        draft.threads='9';draft.readAhead='off';draft.nocache='on';draft.lru='default';draft.cache='';
        draft.sources=draft.sources.slice(0,3).map((s,i)=>({...s,weight:i?'1':'2',pieces:'1',inflight:'0'}));
        draft.method=draft.sources.length>1?'split':'single';
      }
      if(draft.build==='standard'){draft.method='single';draft.nocache='default';draft.lru='default';draft.sources=draft.sources.slice(0,1).map(s=>({...s,weight:'1',pieces:'1',inflight:'0'}));}
      mountEngineSettings(container,host);
      container.querySelector(`[name="${e.target.name}"]`)?.focus({preventScroll:true});
    }
  });
  form.addEventListener('submit',e=>{e.preventDefault();review();});
  container.onclick=async e=>{
    const target=e.target.closest('[data-engine-action]');if(!target)return;
    const action=target.dataset.engineAction;
    if(action==='add-source'){read();const limit=draft.modelFamily==='deepseek41'?3:8;if(draft.sources.length>=limit){message(`This profile supports at most ${limit} sources.`,true);return;}draft.sources.push({path:'',weight:'1',pieces:'1',inflight:'0'});draft.method='split';mountEngineSettings(container,host);}
    else if(action==='remove-source'){read();draft.sources.splice(Number(target.dataset.index),1);if(draft.sources.length===1)draft.method='single';mountEngineSettings(container,host);}
    else if(action==='save'){read();target.disabled=true;try{checkedDraft(draft);if(host.saveDraft){await host.saveDraft(structuredClone(draft));message('Draft saved on this Mac. No engine settings were changed.');}else{localStorage.setItem(storageKey,JSON.stringify(draft));message('Draft saved in this browser. No engine settings were changed.');}}catch(err){message(err.message,true);}finally{target.disabled=false;}}
    else if(action==='export-json'||action==='export-env'){
      if(!reviewed)return;
      const json=action==='export-json',blob=new Blob([json?JSON.stringify(reviewed,null,2)+'\n':engineEnvironment(reviewed)],{type:json?'application/json':'text/plain'});
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=json?'argodrive-ds4-candidate.json':'argodrive-ds4-candidate.env';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  };
}
