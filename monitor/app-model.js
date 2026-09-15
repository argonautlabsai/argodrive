// Shared, dependency-free report semantics. Missing measurements stay missing.
export const finite = v => typeof v === 'number' && Number.isFinite(v);
export const fmt = (v, digits = 2) => finite(v) ? v.toLocaleString('en-GB', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
export const length = r => r?.tokens ?? r?.tokens_inferred ?? r?.generated;
export const runId = r => `${r.block}/${r.arm}`;
export const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function flatten(blocks = []) {
  return blocks.flatMap(b => b.rows.map(r => ({...r, block:b.block}))).sort((a,b) => String(b.ran || '').localeCompare(String(a.ran || '')) || runId(a).localeCompare(runId(b)));
}
export function compare(a,b) {
  if (!a || !b) return {matched:false, checks:[], differences:[], reason:'Choose two runs to compare.'};
  const known = v => v !== null && v !== undefined && v !== '' && v !== 'unknown';
  const pair = (label,x,y) => ({label, a:x, b:y, state: !known(x) || !known(y) ? 'unknown' : x === y ? 'match' : 'different'});
  const checks = [pair('Engine',a.engine,b.engine),pair('Model',a.model,b.model),pair('Prompt',a.prompt_hash,b.prompt_hash),pair('Requested output',a.tokens,b.tokens),pair('Generated output',a.generated,b.generated)];
  ['Context','Prompt format','Temperature','Thinking'].forEach((label,i) => checks.push(pair(label,a.comparison_context?.[i],b.comparison_context?.[i])));
  checks.push({label:'Run completion',a:a.incomplete ? 'Incomplete':'Complete',b:b.incomplete ? 'Incomplete':'Complete',state: !a.incomplete && !b.incomplete && finite(a.tok_s) && finite(b.tok_s) ? 'match':'different'});
  const matched = runId(a) !== runId(b) && checks.every(c => c.state === 'match');
  return {matched, checks, reason: matched ? 'Recorded workload fields match' : runId(a) === runId(b) ? 'Choose two different runs' : 'Workload match is not established',
    output: a.output_hash && b.output_hash ? a.output_hash === b.output_hash ? 'same' : 'different' : 'unknown'};
}
export function gain(a,b,lower=false) {
  if (!finite(a) || !finite(b) || a <= 0 || b < 0) return null;
  return (b-a)/a*100*(lower ? -1:1);
}
export function settingsDiff(a={},b={}) {
  return [...new Set([...Object.keys(a),...Object.keys(b)])].sort().filter(k => a[k] !== b[k]).map(k => ({key:k,a:a[k],b:b[k]}));
}
export function filterRuns(rows,{query='',engine='',tokens='',status=''}={}) {
  return rows.filter(r => (!query || [r.arm,r.block,r.model,r.prompt].join(' ').toLowerCase().includes(query.toLowerCase())) && (!engine || r.engine===engine) && (!tokens || String(length(r))===tokens) && (!status || (status==='complete')===!r.incomplete));
}
export const runExportOptions = [
  ['all','All runs'],
  ['hours:1','Last 1 hour'],['hours:3','Last 3 hours'],['hours:6','Last 6 hours'],['hours:24','Last 24 hours'],
  ['last:1','Latest 1 run'],['last:5','Latest 5 runs'],['last:10','Latest 10 runs'],['last:20','Latest 20 runs']
];
export function runExportURL(scope) {
  if (!runExportOptions.some(([value])=>value===scope)) throw Error('Choose an export range.');
  if (scope==='all') return '/stats.csv';
  const [key,value]=scope.split(':');
  return `/runs-export.csv?${key}=${value}`;
}
export function chartPath(points,width,height,minX,maxX,maxY) {
  return points.filter(p => finite(p[0]) && finite(p[1])).map((p,i) => `${i?'L':'M'}${((p[0]-minX)/(maxX-minX||1)*width).toFixed(2)},${(height-p[1]/(maxY||1)*height).toFixed(2)}`).join(' ');
}

// One axis for every live drive card. The 16 GB/s floor prevents idle drives
// looking saturated; larger observations expand every card together.
export function sharedReadBarScale(devices=[],traces={},caps={}) {
  const points=devices.flatMap(d=>(traces[d.id]||[]).filter(p=>finite(p[0])&&finite(p[1])&&p[1]>=0));
  const end=Math.max(0,...points.map(p=>p[0]));
  const rates=points.filter(p=>p[0]>=end-120).map(p=>p[1]);
  const max=Math.max(0,...rates,...devices.map(d=>finite(caps[d.id])?caps[d.id]:0));
  return {end,ceiling:Math.max(16,Math.ceil(max*1.05/4)*4)};
}

// The Monitor per-drive cards share ONE axis so drives stay comparable, and that
// axis fits the window: 10% above the busiest drive's average, never below any
// drive's peak so nothing clips (KP, 2026-09-15). MONITOR_READ_SCALE_GBPS is the
// floor used when no drive has reported a window yet (idle machine, first tick).
export const MONITOR_READ_SCALE_GBPS = 16;
export const MONITOR_READ_HEADROOM = 1.10;
export function monitorReadBarScale(devices=[],traces={},seconds=20,end=null) {
  const points=devices.flatMap(d=>traces[d.id]||[]);
  const last=finite(end)?end:Math.max(0,...points.filter(p=>finite(p[0])).map(p=>p[0]));
  let ceiling=0;
  for(const d of devices){
    const s=readWindowStats(traces[d.id]||[],seconds,last);
    if(!s.points.length) continue;
    ceiling=Math.max(ceiling,(s.mean||0)*MONITOR_READ_HEADROOM,s.peak||0);
  }
  return {end:last,ceiling:ceiling>0?Math.max(4,Math.ceil(ceiling)):MONITOR_READ_SCALE_GBPS,mode:'shared'};
}

export function readWindowStats(points=[],seconds=120,end=null) {
  const valid=points.filter(p=>finite(p[0])&&finite(p[1])&&p[1]>=0&&finite(p[2])&&p[2]>0);
  end=finite(end)?end:valid.length?Math.max(...valid.map(p=>p[0])):null;
  const rows=valid.filter(p=>p[0]<=end&&p[0]-p[2]>=end-seconds);
  const duration=rows.reduce((n,p)=>n+p[2],0),bytes=rows.reduce((n,p)=>n+p[1]*p[2],0);
  const peaks=rows.filter(p=>p[2]<=.35);
  return {mean:duration?bytes/duration:null,peak:peaks.length?Math.max(...peaks.map(p=>p[1])):null,
          seconds:duration,points:rows,end,reading:rows.some(p=>p[0]>=end-3&&p[1]>.001)};
}

// Display scaling only: use the visible read intervals, never a drive's advertised ceiling.
export function readAutoScale(devices=[],traces={},seconds=20,end=null,mode='shared') {
  const points=devices.flatMap(d=>traces[d.id]||[]);
  const last=finite(end)?end:Math.max(0,...points.filter(p=>finite(p[0])).map(p=>p[0]));
  const visible=readWindowStats(points,seconds,last).points;
  const peak=Math.max(0,...visible.map(p=>p[1]));
  return {end:last,ceiling:Math.max(2,Math.ceil(peak*1.15/2)*2),mode};
}
