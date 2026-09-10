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
export function chartPath(points,width,height,minX,maxX,maxY) {
  return points.filter(p => finite(p[0]) && finite(p[1])).map((p,i) => `${i?'L':'M'}${((p[0]-minX)/(maxX-minX||1)*width).toFixed(2)},${(height-p[1]/(maxY||1)*height).toFixed(2)}`).join(' ');
}
