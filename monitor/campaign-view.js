import {escapeHTML as esc, fmt, finite} from './app-model.js';

const badge=(text,kind='')=>`<span class="badge ${kind}">${esc(text)}</span>`;
const nice=name=>({
  'workers-36':'36 reader threads','workers-64':'64 reader threads','hub-cap-4':'Hub in-flight limit: 4',
  'hub-cap-8':'Hub in-flight limit: 8','balance-D5':'9:6:6:3:3 block allocation','balance-E5':'9:7:7:2:2 block allocation',
  'cache-70GB':'70GB expert-cache budget','q8-nsg-2':'Q8 dispatch: 2 SIMD groups','q8-nsg-8':'Q8 dispatch: 8 SIMD groups'
}[name]||String(name||'Unlabelled experiment'));
const stateLabel=status=>({running:'Running',preparing:'Preparing',complete:'Completed',stopped:'Stopped'}[status]||'Status unavailable');
const time=value=>{const d=new Date(value);return value&&!Number.isNaN(d.valueOf())?d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Not recorded';};
export function selectedCampaign(data,id=''){return data?.campaigns?.find(c=>c.id===id)||data?.campaigns?.[0]||null;}

function speedChart(groups){
  const maximum=Math.max(6,...groups.flatMap(g=>[g.control?.median||0,g.candidate?.median||0]));
  return `<div class="campaign-speed-chart" aria-label="Recorded median generation speeds. Target is 5 tokens per second.">${groups.map(g=>`
    <div class="campaign-speed-group"><div class="campaign-speed-label"><strong>${esc(g.tokens)} generated tokens</strong>${badge(g.three_pairs_complete?'3 matched pairs':'Incomplete comparison',g.three_pairs_complete?'green':'amber')}</div>
    ${[['control','Four drives'],['candidate','Five drives']].map(([key,label])=>{const s=g[key]||{};return `<div class="campaign-bar-row"><span>${label}</span><div class="campaign-bar-track"><i class="campaign-bar ${key}" style="width:${finite(s.median)?Math.max(0,Math.min(100,s.median/maximum*100)):0}%"></i><i class="campaign-target" style="left:${5/maximum*100}%" title="5 tok/s target"></i></div><strong>${fmt(s.median,3)} <small>tok/s</small></strong></div>`;}).join('')}
    <p class="chart-note">${g.three_pairs_complete&&finite(g.median_gain_pct)?`${g.median_gain_pct>=0?'+':''}${fmt(g.median_gain_pct,2)}% median gain · worst pair ${g.worst_pair_gain_pct>=0?'+':''}${fmt(g.worst_pair_gain_pct,2)}%`:'More valid pairs are needed before evaluating the gain.'}${g.candidate?.range?`<br>Five-drive range: ${fmt(g.candidate.range[0],3)}–${fmt(g.candidate.range[1],3)} tok/s`:''}</p></div>`).join('')}
    <p class="chart-note">Bars start at zero. Dashed marker: 5 tok/s target. Native engine rate uses actual generated tokens / its generation timer.</p></div>`;
}

export function campaignView(data,{selected=''}={}){
  const c=selectedCampaign(data,selected);
  if(!c)return `<section class="panel"><div class="panel-body campaign-empty"><h2>No tuning campaigns in this folder</h2><p>Connect the folder containing your Argodrive campaign results. Individual engine runs remain available in Recorded arms.</p><a class="button" href="#settings">Choose run folder</a><p class="chart-note">This view reads saved campaign records. It does not start a benchmark or change engine settings.</p>${(data?.errors||[]).map(e=>`<p class="data-error">${esc(e)}</p>`).join('')}</div></section>`;
  const qualified=c.recorded_qualification_passed===true;
  const target=c.target_5_met===true&&qualified;
  const rates=(c.groups||[]).map(g=>g.candidate?.median).filter(finite);
  const floor=qualified&&rates.length===2?Math.min(...rates):null;
  const need=finite(floor)&&floor>0?Math.max(0,(5/floor-1)*100):null;
  return `<div class="campaign-toolbar"><label for="campaign-select">Campaign<select id="campaign-select">${data.campaigns.map(x=>`<option value="${esc(x.id)}" ${x.id===c.id?'selected':''}>${esc(time(x.started_at))} · ${esc(stateLabel(x.status))} · ${esc(x.id.startsWith('argodrive-refine-')?'Refinement':'Layout & scheduling')}</option>`).join('')}</select></label><button class="button" data-campaign-export="${esc(c.id)}">Export campaign report</button></div>
    <section class="panel campaign-status"><div class="panel-body"><div class="campaign-status-head"><div><div class="eyebrow">MEASURED OPTIMIZATION</div><h2>${esc(stateLabel(c.status))}${c.active_stage?' · '+esc(c.active_stage.replace(/^\d+-/, '').replaceAll('-',' ')):''}</h2><p>${esc(c.id)}</p></div>${badge(target?'5 tok/s measured at both lengths':qualified?'Below 5 tok/s target':'Qualification pending',target?'green':qualified?'amber':'purple')}</div>
    <div class="campaign-facts"><div><span>Completed arms</span><strong>${esc(c.completed_arms)}</strong><small>All recorded stages</small></div><div><span>Selected layout</span><strong>${esc(c.candidate||'Pending')}</strong><small>Candidate for qualification</small></div><div><span>Gain still needed for 5</span><strong>${finite(need)?fmt(need,1)+'%':'—'}</strong><small>${qualified?'From the slower qualified length':'Waiting for complete qualification'}</small></div><div><span>Deadline</span><strong class="campaign-time">${esc(time(c.deadline))}</strong><small>Recorded campaign stop time</small></div></div>
    ${c.active_arm?`<div class="campaign-active"><span class="status-dot"></span><span>Current arm <strong>${esc(c.active_arm)}</strong></span><a class="button small" href="#runmonitor">Watch SSD reads</a></div><p class="chart-note">Saved activity ${finite(c.activity_age_s)?fmt(c.activity_age_s,0)+' seconds ago':'time unavailable'}. ${c.activity_age_s>30?'The recorded running state may be stale.':'Status is reported by the runner; this view does not inspect its process.'}</p>`:''}
    ${c.error?`<p class="data-error">${esc(c.error)}</p>`:''}${(c.issues||[]).map(e=>`<p class="data-error">${esc(e)}</p>`).join('')}
    </div></section>
    <div class="two-col equal"><section class="panel"><div class="panel-head"><div><h2>Long-run qualification</h2><p>128 and 512 generated tokens · repeated controls</p></div>${badge(qualified?'Recorded checks passed':'Provisional',qualified?'green':'amber')}</div>
    <div class="panel-body">${c.groups?.length?speedChart(c.groups):'<div class="campaign-empty"><h3>Long-run results will appear here</h3><p>Short screens choose the next experiment. They do not qualify a speed headline.</p></div>'}</div></section>
    <section class="panel"><div class="panel-head"><div><h2>What helped?</h2><p>Each setting is tested against its current control.</p></div></div>
    ${c.tuning?.length?`<div class="table-scroll"><table class="tight-table"><thead><tr><th>Experiment</th><th class="cell-num">Median change</th><th>Decision</th></tr></thead><tbody>${c.tuning.map(t=>`<tr><td>${esc(nice(t.name))}</td><td class="cell-num">${finite(t.gain_pct)?`${t.gain_pct>=0?'+':''}${fmt(t.gain_pct,2)}%`:'Not established'}</td><td>${badge(t.retained?'Kept for qualification':'Not retained',t.retained?'green':'')}</td></tr>`).join('')}</tbody></table></div>`:'<div class="panel-body campaign-empty"><h3>Experiments are in progress</h3><p>Decisions appear after the matched comparison finishes.</p></div>'}
    <div class="panel-body"><p class="chart-note">A positive short-run result still needs long-run qualification. Different output, incomplete runs and failed checks cannot qualify.</p></div></section></div>
    <section class="panel"><div class="panel-head"><div><h2>Recorded stages</h2><p>Progress comes from the controller’s saved records.</p></div></div><div class="campaign-stages">${(c.stages||[]).map(s=>`<div><span>${esc(s.name.replace(/^\d+-/, '').replaceAll('-',' '))}</span><strong>${esc(s.completed)} / ${esc(s.planned)} arms</strong>${badge(stateLabel(s.status),s.status==='complete'?'green':s.status==='stopped'?'amber':'purple')}</div>`).join('')}</div></section>
    <section class="panel"><div class="panel-body"><details><summary>Candidate settings & measurement scope</summary><pre>${esc(JSON.stringify(c.settings,null,2))}</pre><p class="chart-note">${esc(c.source_note)} Matching output is not an answer-quality evaluation. Model checksums, quantization and realistic prompts must be reviewed before publication. Settings have not been applied by this view.</p><p class="chart-note">Binary SHA-256: ${esc((c.engine_sha256||[]).join(', ')||'Not recorded')}</p></details></div></section>`;
}

export function campaignExport(c){
  return JSON.stringify({schema:'argodrive-campaign-review-v1',exported_at:new Date().toISOString(),campaign:c,
    note:'Saved measurement records and candidate settings. This JSON is not an executable script and does not establish publication readiness.'},null,2)+'\n';
}
