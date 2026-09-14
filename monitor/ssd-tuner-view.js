import {escapeHTML as esc,fmt} from './app-model.js';

// Public intake address. Mail is still composed locally and never sent by the app.
export const BENCHMARKS_EMAIL='benchmarks@argonautlabs.ai';
export const BENCHMARKS_ISSUE_URL='https://github.com/argonautlabsai/argodrive/issues/new';

export function tunerForm(){return `<section class="panel"><div class="panel-head"><div><h2>Find my best SSD settings</h2><p>Up to five minutes of read-only storage testing. No inference or automatic configuration changes.</p></div><span class="badge">Read only</span></div><div class="panel-body">
<form id="ssd-tuner-form"><div class="engine-fields"><label class="field"><span>Model family</span><select name="model_family"><option value="deepseek41">DeepSeek V4.1 Flash</option><option value="deepseek">DeepSeek V4</option><option value="glm">GLM</option><option value="other">Other Q4_K MoE</option></select></label><label class="field"><span>Engine build</span><select name="engine_build"><option value="upstream">Upstream ds4</option><option value="argonaut">Argonaut ds4 fork</option></select></label><label class="field"><span>Engine commit / build identity · optional</span><input name="build_identity" maxlength="160" placeholder="Exact commit or binary hash"></label></div>
<label class="field"><span>Existing model files · one per physical SSD</span><textarea name="paths" rows="4" required spellcheck="false" placeholder="/path/to/model.gguf&#10;/Volumes/Enclosure/model.gguf" aria-describedby="ssd-tuner-file-note"></textarea></label>
<p id="ssd-tuner-file-note" class="chart-note">One full GGUF path per line, up to five drives. Matching Q4_K routed-expert layouts are required. Model files are only read; reports are stored in Argodrive’s app data.</p>
<p class="info-note">Tests each SSD, shared hubs and the selected drives together. Measures request latency and application reads with physical-device counters alongside. Keep other disk workloads idle. The final report proposes settings; a model benchmark must confirm token-speed gains.</p>
<div class="button-group"><button type="submit" class="button primary" id="ssd-tuner-start">Start 5-minute SSD test</button><button type="button" class="button" id="ssd-tuner-stop" hidden>Stop test</button></div></form>
<p role="status" id="ssd-tuner-message">Ready. No drive test starts until you press Start.</p><div id="ssd-tuner-progress"></div><div id="ssd-tuner-result"></div></div></section>`;}

export function tunerProgress(s){
 const p=Math.max(0,Math.min(100,Number(s.progress_percent)||0));
 return `<progress max="100" value="${p}" aria-label="SSD test progress" style="width:100%"></progress><p class="chart-note">${fmt(p,0)}% · ${s.trials_done||0} / ${s.trial_count||'—'} trials · ${fmt(s.elapsed_s,0)} s elapsed</p>`+
 ((s.current_devices||[]).length?`<div class="table-scroll"><table class="tight-table"><thead><tr><th>Physical SSD</th><th>Current trial average</th><th>Reads</th></tr></thead><tbody>${s.current_devices.map(d=>`<tr><td>${esc(d.disk)}</td><td>${fmt(d.gbps)} GB/s</td><td>${d.reads}</td></tr>`).join('')}</tbody></table></div>`:'');
}
export function tunerResult(s){
 if(s.status!=='complete'||!s.report)return '';
 const r=s.report.recommendation;
 return `<div class="info-note" style="margin:16px 0"><strong>Recommended settings · awaiting model validation</strong><br>${esc(r.confidence)}. No token-speed gain is predicted.</div><div class="engine-fields"><div><span class="eyebrow">TESTER WORKERS / DRIVE</span><h3>${r.workers_per_drive}</h3></div><div><span class="eyebrow">CANDIDATE SPLIT WEIGHTS</span><h3>${r.candidate_weights.join(' : ')}</h3></div><div><span class="eyebrow">REPEATED AGGREGATE READS</span><h3>${r.confirmation_range_gbps?.map(v=>fmt(v)).join('–')||'—'} GB/s</h3></div></div><p class="chart-note">Weights follow your file order. Multi-drive settings require a compatible engine. Engine threads and per-drive test workers are different settings.</p><div class="button-group"><button class="button primary" id="ssd-tuner-github">Share on GitHub</button><button class="button" id="ssd-tuner-copy">Copy instructions for Claude</button><button class="button" id="ssd-tuner-text">Export text report</button><button class="button" id="ssd-tuner-json">Export raw measurements</button><button class="button" id="ssd-tuner-email">Compose email report</button><button class="button" id="ssd-tuner-verify">Open model test setup</button></div><p class="chart-note">Share on GitHub opens a redacted issue draft in the Argodrive repository. Review it before submitting. Email opens your default mail app addressed to the public intake.</p><details style="margin-top:12px"><summary>Review the handover</summary><textarea readonly rows="18" style="width:100%;margin-top:10px" aria-label="Claude handover report">${esc(s.report_text||'')}</textarea></details>`;
}
export function emailReportText(report){
 const q=report?.request||{},r=report?.recommendation||{};
 const drives=(report?.sources||[]).map(s=>s.label||s.disk||'drive').join(', ')||'not recorded';
 const trials=(report?.trials||[]).filter(t=>t.phase==='confirm').map(t=>`${fmt(t.aggregate_gbps)} GB/s`).join(' / ')||'not recorded';
 return ['Argodrive SSD benchmark report','',`Model family: ${q.model_family||'not recorded'}`,`Engine build: ${q.engine_build||'not recorded'}`,`Drives tested: ${drives}`,`Confirmation reads: ${trials}`,`Candidate workers per drive: ${r.workers_per_drive??'not recorded'}`,`Candidate split weights: ${(r.candidate_weights||[]).join(' : ')||'not recorded'}`,`Confidence: ${r.confidence||'not recorded'}`,'','This summary was generated locally by Argodrive. Local file paths, volume UUIDs and serial numbers were omitted. Exact token speed requires a separate model verification run.',''].join('\n');
}
export function githubIssueText(report){
 const q=report?.request||{},r=report?.recommendation||{};
 const drives=(report?.sources||[]).map(s=>s.label||s.disk||'drive').join(', ')||'not recorded';
 const trials=(report?.trials||[]).filter(t=>t.phase==='confirm').map(t=>`${fmt(t.aggregate_gbps)} GB/s`).join(' / ')||'not recorded';
 return ['## Benchmark summary','',`- **Model family:** ${q.model_family||'not recorded'}`,`- **Engine build:** ${q.engine_build||'not recorded'}`,`- **Drives tested:** ${drives}`,`- **Confirmation read rate:** ${trials}`,`- **Candidate workers per drive:** ${r.workers_per_drive??'not recorded'}`,`- **Candidate split weights:** ${(r.candidate_weights||[]).join(' : ')||'not recorded'}`,`- **Confidence:** ${r.confidence||'not recorded'}`,'','This summary was generated by Argodrive. It intentionally omits local paths, volume UUIDs, drive serial numbers, prompts and usernames. Exact token speed requires a separate model verification run.',''].join('\n');
}
export function githubIssueUrl(report){
 const q=report?.request||{};
 const u=new URL(BENCHMARKS_ISSUE_URL);u.searchParams.set('template','benchmark-result.md');u.searchParams.set('title',`[Benchmark] ${q.model_family||'SSD streaming'}`);u.searchParams.set('body',githubIssueText(report));return u.toString();
}
export function mountSSDTuner(container,host){
 container.innerHTML=tunerForm();let state={status:'idle'},pending=false,inFlight=false;
 const get=id=>container.querySelector(id),form=get('#ssd-tuner-form');
 const message=text=>get('#ssd-tuner-message').textContent=text;
 const post=(path,body)=>host.api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':host.token},body:JSON.stringify(body)});
 function paint(){
   const busy=state.owned||pending;
   for(const input of form.querySelectorAll('input,textarea,select'))input.disabled=busy;
   get('#ssd-tuner-start').disabled=busy;get('#ssd-tuner-stop').hidden=!state.owned;
   message((state.error?state.error+' · ':'')+(state.message||'Ready. No drive test starts until you press Start.'));
   get('#ssd-tuner-progress').innerHTML=state.status==='idle'?'':tunerProgress(state);
   get('#ssd-tuner-result').innerHTML=tunerResult(state);
   if(get('#ssd-tuner-copy'))get('#ssd-tuner-copy').onclick=async()=>{try{await navigator.clipboard.writeText(state.report_text);message('Instructions copied. Paste them into your Claude project session.');}catch{const area=get('#ssd-tuner-result textarea');area.closest('details').open=true;area.focus();area.select();message('Select and copy the report below.');}};
   if(get('#ssd-tuner-text'))get('#ssd-tuner-text').onclick=()=>host.download('argodrive-ssd-tuning.txt',state.report_text,'text/plain');
   if(get('#ssd-tuner-email'))get('#ssd-tuner-email').onclick=()=>{const subject=`Argodrive SSD benchmark · ${state.report?.request?.model_family||'model'}`;const body=emailReportText(state.report);window.location.href=`mailto:${BENCHMARKS_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;};
   if(get('#ssd-tuner-verify'))get('#ssd-tuner-verify').onclick=()=>host.onVerify?.();
   if(get('#ssd-tuner-json'))get('#ssd-tuner-json').onclick=()=>host.download('argodrive-ssd-tuning.json',JSON.stringify(state.report,null,2)+'\n','application/json');
   if(get('#ssd-tuner-github'))get('#ssd-tuner-github').onclick=()=>{window.open(githubIssueUrl(state.report),'_blank','noopener');message('GitHub issue draft opened. Review the redacted summary before submitting.');};
 }
 async function poll(){if(!container.isConnected||inFlight||pending)return;inFlight=true;try{const next=await host.api('/ssd-tuner');if(container.isConnected){const changed=JSON.stringify(next)!==JSON.stringify(state);state=next;if(changed)paint();}}catch(e){if(container.isConnected)message(e.message);}finally{inFlight=false;}}
 form.onsubmit=async e=>{e.preventDefault();if(pending||state.owned)return;const fields=new FormData(form);pending=true;paint();message('Checking for active benchmarks and model copies…');try{state=await post('/ssd-tuner/start',{paths:String(fields.get('paths')).split('\n').map(p=>p.trim()).filter(Boolean),model_family:fields.get('model_family'),engine_build:fields.get('engine_build'),build_identity:String(fields.get('build_identity')).trim()});}catch(e){state={status:'failed',error:e.message};}finally{pending=false;if(container.isConnected)paint();}};
 get('#ssd-tuner-stop').onclick=async()=>{if(!state.owned)return;try{state=await post('/ssd-tuner/stop',{id:state.id});message('Stopping this SSD test; partial results will be retained.');}catch(e){message(e.message);}};
 poll();const timer=setInterval(()=>{if(!container.isConnected)clearInterval(timer);else if(!document.hidden)poll();},1000);
}
