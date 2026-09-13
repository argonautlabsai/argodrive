import {escapeHTML as esc} from './app-model.js';
const gib=n=>Number.isFinite(n)?(n/2**30).toFixed(1)+' GiB':'—';
export function supportView(data){
  return `<div class="model-support-grid">${data.models.map(m=>`<section class="panel"><div class="panel-body"><span class="eyebrow">${esc(m.engine)}</span><h2>${esc(m.label)}</h2><span class="badge ${m.id==='deepseek41'?'amber':''}">${esc(m.status)}</span><p>${esc(m.description)}</p>${m.streaming_method?`<p><strong>Streaming method</strong><br>${esc(m.streaming_method)}</p>`:''}${m.streaming_reference?kimiReferenceView(m.streaming_reference):''}${m.benchmark_evidence?`<p><strong>${Number(m.benchmark_evidence.median_tok_s.fork_plus_two).toFixed(2)} tok/s</strong> · local three-drive median</p><p class="chart-note">${esc(m.benchmark_evidence.hardware)}<br>pp${Number(m.benchmark_evidence.prompt_tokens)} / tg${Number(m.benchmark_evidence.generated_tokens)} · ${Number(m.benchmark_evidence.repetitions)} repetitions · includes first decode step</p><details><summary>Measurement scope</summary><p class="chart-note">${esc(m.benchmark_evidence.scope)}</p></details>`:''}</div></section>`).join('')}</div>
  <section class="panel"><div class="panel-head"><div><h2>Prepare DeepSeek V4.1 Flash Q4</h2><p>Check the download and plan two enclosure copies before testing.</p></div><span class="badge amber">Preparation only</span></div><div class="panel-body">
  <div class="model-memory-grid"><div><span class="eyebrow">MODEL FILE</span><strong>483 GiB</strong></div><div><span class="eyebrow">MAIN WEIGHTS</span><strong>294 GiB</strong></div><div><span class="eyebrow">DISK-ONLY ENGRAM</span><strong>189 GiB</strong></div></div>
  <p class="info-note">On a 128 GB Mac, start with SSD streaming and automatic expert cache sizing. The experimental Argodrive ds4 fork can split expert reads across two enclosures while reading Engram rows on the primary SSD. This profile requires the fork; upstream ds4 and the GLM fork use different capabilities and settings.</p>
  <form id="model-readiness-form"><label class="field"><span>Final Q4 model path</span><input name="model_path" required placeholder="/path/to/DeepSeek-V4.1-Flash-Q4.gguf" spellcheck="false"></label><div class="engine-fields">${[1,2].map(n=>`<label class="field"><span>Enclosure ${n} directory · optional</span><input name="replica${n}" placeholder="/Volumes/Drive" spellcheck="false"></label>`).join('')}</div><button class="button primary" type="submit">Check readiness and space</button><p class="chart-note">Reads eight header bytes and filesystem metadata. Does not hash, copy, delete, or launch a test.</p><p role="status" id="model-readiness-status"></p></form><div id="model-readiness-result"></div></div></section>`;
}

function kimiReferenceView(r){
  const env=r.recommended_environment||{};
  const ladder=r.drive_ladder||{};
  return `<details class="model-reference"><summary>Review Deltafin Kimi candidate</summary><p class="chart-note">Fork ${esc(r.base_commit)} · ${esc(r.scope)}</p><div class="model-memory-grid"><div><span class="eyebrow">1 DRIVE</span><strong>${Math.round(Number(ladder.one_drive_fraction_of_four)*100)}%</strong><small>of four-drive speed</small></div><div><span class="eyebrow">2 DRIVES</span><strong>${Math.round(Number(ladder.two_drive_fraction_of_four)*100)}%</strong><small>of four-drive speed</small></div><div><span class="eyebrow">3 DRIVES</span><strong>${Math.round(Number(ladder.three_drive_fraction_of_four)*100)}%</strong><small>of four-drive speed</small></div></div><div class="table-scroll"><table class="tight-table"><thead><tr><th>Deltafin setting</th><th>Candidate value</th></tr></thead><tbody>${Object.entries(env).map(([k,v])=>`<tr><td><code>${esc(k)}</code></td><td><code>${esc(v)}</code></td></tr>`).join('')}</tbody></table></div><p class="chart-note">Candidate only. Argodrive does not apply or copy weights; verify replica hashes, physical drives and a matched A/B run first.</p></details>`;
}
export function readinessView(r){
  return `<h3>${r.size_and_header_match?'File size and header match · verification still required':'Model not ready'}</h3><p>${gib(r.file_bytes)} present · expected ${gib(r.expected_bytes)}</p>
  ${r.placements.length?`<div class="table-scroll"><table class="tight-table"><thead><tr><th>Enclosure directory</th><th>Replica metadata</th><th>Free</th><th>Additional space needed</th><th>Capacity / topology</th></tr></thead><tbody>${r.placements.map(p=>`<tr><td>${esc(p.directory)}</td><td>${p.existing_size_and_header_match?'Present · hash required':'Full copy required'}</td><td>${gib(p.free_bytes)}</td><td>${gib(p.additional_bytes_needed)}</td><td>${p.error?esc(p.error):!p.distinct_filesystem?'Same filesystem · check topology':p.has_capacity?'Space available':'Insufficient space'}</td></tr>`).join('')}</tbody></table></div>`:''}
  ${r.experimental_profile?profileView(r.experimental_profile):''}
  <ul>${r.checks.concat(r.notes).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><button type="button" class="button" id="export-model-readiness">Export preparation report</button>`;
}
export function profileView(p){
  return `<section class="panel"><div class="panel-head"><div><h3>DeepSeek engine profile</h3><p>${esc(p.status)} · ${esc(p.engine)}</p></div><span class="badge amber">Not applied</span></div><div class="panel-body"><p>${esc(p.expert_policy)}. ${esc(p.engram_policy)}. ${esc(p.cache_policy)}.</p><p class="chart-note">Starting settings for this fork and model. Compare matched runs on your hardware before adopting them. Export includes structured settings; it does not start inference.</p>${p.errors.length?`<p role="alert">${p.errors.map(esc).join(' ')}</p>`:''}<details><summary>Review ${Object.keys(p.environment).length} engine settings</summary><div class="table-scroll"><table class="tight-table"><thead><tr><th>Setting</th><th>Value</th></tr></thead><tbody>${Object.entries(p.environment).map(([k,v])=>`<tr><td><code>${esc(k)}</code></td><td><code>${esc(v)}</code></td></tr>`).join('')}</tbody></table></div></details></div></section>`;
}
export async function mountModelSupport(container,host){
  try{
    const data=await host.api('/models');if(!container.isConnected)return;
    container.innerHTML=supportView(data);
    const form=container.querySelector('form'),status=container.querySelector('#model-readiness-status');
    form.onsubmit=async e=>{
      e.preventDefault();const input=new FormData(form),button=form.querySelector('button');button.disabled=true;status.textContent='Checking file metadata…';
      container.querySelector('#model-readiness-result').innerHTML='';
      try{
        const report=await host.api('/models/preflight',{method:'POST',headers:{'Content-Type':'application/json','X-Argodrive-Token':host.token},body:JSON.stringify({model_path:String(input.get('model_path')).trim(),replica_directories:[input.get('replica1'),input.get('replica2')].map(x=>String(x).trim()).filter(Boolean)})});
        if(!container.isConnected)return;
        container.querySelector('#model-readiness-result').innerHTML=readinessView(report);status.textContent='Preparation report ready. No test started.';
        container.querySelector('#export-model-readiness').onclick=()=>host.download('argodrive-deepseek41-preparation.json',JSON.stringify(report,null,2)+'\n','application/json');
      }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
    };
  }catch(error){if(container.isConnected)container.textContent='Model support unavailable: '+error.message;}
}
