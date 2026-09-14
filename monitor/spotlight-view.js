import {escapeHTML as esc} from './app-model.js';

const labels={enabled:'Indexing on',disabled:'Indexing off',search_disabled:'Search & indexing disabled',unavailable:'Status unavailable'};

export function spotlightPanel(data,loading=false,error=''){
  const rows=data?.volumes||[],job=data?.job,busy=loading||data?.scanning||job?.status==='running';
  const on=rows.filter(r=>r.state==='enabled').length;
  const stamp=data?.checked_at?new Date(data.checked_at*1000).toLocaleTimeString():null;
  return `<section class="panel spotlight-panel" aria-labelledby="spotlight-title">
    <div class="panel-head"><div><h2 id="spotlight-title">Spotlight indexing</h2><p>${stamp?`Checked ${esc(stamp)} · ${rows.length} volumes · ${on} with indexing on`:'Check background indexing before a benchmark'}</p></div>
    <button class="button small" data-spotlight-scan ${busy?'disabled':''}>${loading?'Checking…':'Check drives'}</button></div>
    <div class="panel-body"><p class="info-note">Spotlight indexing can compete with expert reads. Turn it off for a selected model volume, then compare token speed. “On” means indexing is allowed; it does not prove Spotlight is busy.</p>
    ${error?`<p class="data-error" role="alert">${esc(error)}</p>`:''}
    ${job?`<div class="spotlight-result ${job.status==='complete'?'success':''}" role="status"><strong>${esc({running:'Waiting for macOS',complete:'Change verified',cancelled:'Cancelled',failed:'Change failed',unverified:'Check required'}[job.status]||job.status)}</strong><span>${esc(job.message)}</span>${job.history_error?`<span>Local history could not be saved: ${esc(job.history_error)}</span>`:''}</div>`:''}
    ${rows.length?`<div class="spotlight-volumes">${rows.map(r=>`<div class="spotlight-volume"><div class="spotlight-volume-name"><strong>${esc(r.label)}</strong><span>${esc(r.path)}${r.disk?' · '+esc(r.disk):''}</span>${r.system?'<small>Startup volume · affects Spotlight and Finder search updates across this Mac</small>':''}</div><span class="badge ${r.state==='enabled'?'amber':r.state==='disabled'?'green':''}">${esc(labels[r.state]||'Status unavailable')}</span><div>${r.can_change?`<button class="button small" data-spotlight-volume="${esc(r.id)}" data-spotlight-enabled="${r.state==='disabled'?'true':'false'}" ${busy?'disabled':''}>${r.state==='disabled'?'Turn indexing on':'Turn indexing off'}</button>`:'<span class="muted">No change available</span>'}</div><details class="spotlight-evidence"><summary>macOS status</summary><pre>${esc(r.evidence||'No status returned')}</pre></details></div>`).join('')}</div>`:
    '<div class="spotlight-empty"><strong>No drive check yet</strong><p>Check drives to read the current Spotlight setting for mounted volumes, including newly connected SSDs. This also works with hardware sampling off.</p></div>'}
    <p class="chart-note">Changes apply to one volume and remain until changed again. Existing files and indexes are not deleted. Search results may stop updating while indexing is off; turning it back on can generate background I/O. macOS may request administrator authentication.</p>
    </div></section>`;
}

export function spotlightConfirmation(row,enabled){
  return `${enabled?'Turn on':'Turn off'} Spotlight indexing for “${row.label}”?\n\n${row.path}\n\n${enabled?'Spotlight can resume indexing this volume and create background disk activity.':'Spotlight and Finder search results may stop updating for files on this volume. Files and existing indexes are retained.'}${row.system?'\n\nThis is your startup volume. The change affects search updates across this Mac.':''}\n\nThis setting persists until you change it again. Authenticate only in the macOS dialog.`;
}
