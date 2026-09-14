import test from 'node:test';
import assert from 'node:assert/strict';
import {compare,gain,fmt,filterRuns,flatten,settingsDiff,escapeHTML,runExportOptions,runExportURL} from '../monitor/app-model.js';
const baseline={block:'day',arm:'A',engine:'ds4',model:'GLM-Q4',prompt_hash:'abc',tokens:128,generated:128,comparison_context:['4096','1','0','nothink'],tok_s:3.26,tok_s_steady:3.6601,output_hash:'same'};
const candidate={...baseline,arm:'B',tok_s:3.54,tok_s_steady:4.2968};
test('matched pair has 17.4% decode gain; inclusive is distinct',()=>{
  assert.equal(compare(baseline,candidate).matched,true);
  assert.equal(gain(baseline.tok_s_steady,candidate.tok_s_steady).toFixed(1),'17.4');
  assert.notEqual(gain(baseline.tok_s,candidate.tok_s),gain(baseline.tok_s_steady,candidate.tok_s_steady));
});
test('output length, prompt, model and incomplete run cannot qualify a gain',()=>{
  for(const change of [{tokens:512},{generated:512},{prompt_hash:'other'},{model:'other'},{incomplete:true},{engine:'unknown'}])assert.equal(compare(baseline,{...candidate,...change}).matched,false);
});
test('missing workload metadata does not silently count as a match',()=>{
  for(const change of [{tokens:undefined,tokens_inferred:128},{prompt_hash:undefined},{comparison_context:[null,null,null,null]},{comparison_context:['4096','1','0',null]}])assert.equal(compare({...baseline,...change},{...candidate,...change}).matched,false);
  assert.equal(compare(baseline,baseline).matched,false);
});
test('missing is not zero; first-response improvement uses lower-is-better',()=>{
  assert.equal(fmt(null),'—');assert.equal(fmt(undefined),'—');assert.equal(fmt(0),'0.00');
  assert.equal(gain(10,8,true),20);assert.equal(gain(null,8),null);assert.equal(gain(0,2),null);
});
test('output consistency is independent from workload qualification',()=>{
  assert.equal(compare(baseline,{...candidate,output_hash:'different'}).output,'different');
  assert.equal(compare(baseline,{...candidate,output_hash:null}).output,'unknown');
});
test('settings diff retains multiple changes and missing fields',()=>{
  assert.deepEqual(settingsDiff({threads:48,gate:0},{threads:48,gate:2,prefetch:2}),[{key:'gate',a:0,b:2},{key:'prefetch',a:undefined,b:2}]);
});
test('run filters compose and flatten retains source identity',()=>{
  const rows=flatten([{block:'one',rows:[baseline,candidate,{...candidate,arm:'C',incomplete:true,tokens:512}]}]);
  assert.equal(rows[0].block,'one');
  assert.equal(filterRuns(rows,{query:'glm',engine:'ds4',tokens:'128',status:'complete'}).length,2);
  assert.equal(filterRuns(rows,{status:'incomplete'}).length,1);
  assert.equal(filterRuns(rows,{query:'missing'}).length,0);
});
test('saved filenames and prompts are escaped for markup',()=>assert.equal(escapeHTML('<img src=x onerror="x">'), '&lt;img src=x onerror=&quot;x&quot;&gt;'));
test('recent export options never fall back to the unfiltered CSV endpoint',()=>{
  assert.equal(runExportURL('all'),'/stats.csv');
  for(const [scope] of runExportOptions.filter(([scope])=>scope!=='all')){
    const url=new URL(runExportURL(scope),'http://localhost');
    assert.equal(url.pathname,'/runs-export.csv');
    const [key,value]=scope.split(':');
    assert.equal(url.searchParams.get(key),value);
    assert.equal([...url.searchParams].length,1);
  }
  for(const scope of ['',undefined,'last:0','hours:1&last=20'])assert.throws(()=>runExportURL(scope));
});

test('SSD averages weight elapsed time and include sampled idle',async()=>{
  const {readWindowStats}=await import('../monitor/app-model.js');
  const r=readWindowStats([[10.1,10,.1],[10.4,2,.3],[10.6,0,.2]],30,10.6);
  assert.ok(Math.abs(r.mean-1.6/.6)<1e-9);
  assert.equal(r.peak,10);assert.ok(Math.abs(r.seconds-.6)<1e-9);assert.equal(r.reading,true);
});
test('SSD missing and delayed samples do not invent a peak',async()=>{
  const {readWindowStats}=await import('../monitor/app-model.js');
  assert.equal(readWindowStats([]).mean,null);
  assert.equal(readWindowStats([[10,50,3]],30,10).peak,null);
  assert.equal(readWindowStats([[1,10,.2]],30,10).reading,false);
  assert.equal(readWindowStats([[10,10,.2]],2,14).mean,null);
});
