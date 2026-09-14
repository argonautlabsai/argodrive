import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,existsSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {newEngineDraft,checkedDraft,engineProfile,engineEnvironment,engineSettingsView} from '../monitor/engine-settings.js';
const K='DS4_METAL_STREAMING_EXPERT_PREAD_THREADS';
function draft(fork=false){const d=newEngineDraft();d.sources[0].path='/weights/model.gguf';if(fork)d.build='argonaut';return d;}
function split(){const d=draft(true);d.method='split';d.sources[0]={path:'/weights/model.gguf',weight:'10',pieces:'2',inflight:'0'};d.sources.push({path:'/Volumes/One/model.gguf',weight:'5',pieces:'1',inflight:'0'},{path:'/Volumes/Two/model.gguf',weight:'5',pieces:'1',inflight:'0'});return d;}
test('engine and model remain distinct; no draft claims a validated speed gain',()=>{
  const p=engineProfile(draft());assert.equal(p.engine.name,'ds4');assert.equal(p.model.family,'other');
  assert.equal(p.status,'unvalidated');assert.equal(p.validation.speed_gain_percent,null);
  assert.equal(p.engine.installed_binary_verified,false);assert.equal(p.sources[0].physical_device_verified,false);
  assert.deepEqual(p.argv,['--metal','--ssd-streaming','-m','/weights/model.gguf']);
});
test('source-reviewed upstream and fork reader limits are different',()=>{
  const a=draft();a.threads='19';assert.throws(()=>engineProfile(a),/1 to 18/);
  a.threads='18';assert.equal(engineProfile(a).environment[K],'18');
  const b=draft(true);b.threads='64';assert.equal(engineProfile(b).environment[K],'64');
  b.threads='65';assert.throws(()=>engineProfile(b),/1 to 64/);
});
test('presence-based read-ahead off exports 1; default unsets rather than exporting 0',()=>{
  const d=draft(),key='DS4_METAL_DISABLE_STREAMING_EXPERT_READAHEAD';
  assert(engineProfile(d).unset_environment.includes(key));
  d.readAhead='off';assert.equal(engineProfile(d).environment[key],'1');
  d.readAhead='0';assert.throws(()=>engineProfile(d));
});
test('replica settings preserve source order and reject unsupported layouts',()=>{
  const d=split(),p=engineProfile(d);
  assert.equal(p.environment.DS4_MODEL_REPLICAS,'/Volumes/One/model.gguf*5,/Volumes/Two/model.gguf*5');
  assert.equal(p.environment.DS4_MODEL_REPLICA_PIECES_FD,'2,1,1');
  assert.equal(p.environment.DS4_MODEL_PRIMARY_WEIGHT,'10');
  d.build='standard';assert.throws(()=>engineProfile(d),/fork/);
  d.build='argonaut';d.sources[0].weight='60';assert.throws(()=>engineProfile(d),/64 or less/);
  d.sources[0].weight='10';d.sources[1].path=d.sources[0].path;assert.throws(()=>engineProfile(d),/distinct/);
});
test('GLM extensions cannot leak into another model family or non-fork build',()=>{
  const d=split();d.modelFamily='glm';d.prefetch='2';d.prefill='selected';
  assert.equal(engineProfile(d).environment.DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH,'2');
  d.modelFamily='deepseek';assert.throws(()=>engineProfile(d),/GLM controls/);
  d.prefetch='';d.prefill='default';const p=engineProfile(d);
  assert(!('DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH' in p.environment));assert(p.unset_environment.includes('DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH'));
});
test('one-source subdivision activates the global piece plan; inert lookahead is rejected',()=>{
  const d=draft(true);d.sources[0].pieces='2';
  assert.equal(engineProfile(d).environment.DS4_MODEL_REPLICA_PIECES,'2');
  d.modelFamily='glm';d.prefetch='2';engineProfile(d);
  d.sources[0].pieces='1';assert.throws(()=>engineProfile(d),/shared planner/);
  d.prefetch='';d.sources[0].inflight='2';assert.throws(()=>engineProfile(d),/in-flight limits/);
});
test('cache syntax is validated and cold start never claims to disable the RAM cache',()=>{
  const d=draft();d.cache='60GB';d.cold=true;const p=engineProfile(d);
  assert(p.argv.includes('60GB'));assert(p.argv.includes('--ssd-streaming-cold'));
  assert(p.notes.some(x=>x.includes('does not disable')));
  for(const cache of ['0','0GB','-5','NaN','2; echo bad']){d.cache=cache;assert.throws(()=>engineProfile(d));}
});
test('saved drafts may be incomplete but review requires a model; malformed drafts are rejected',()=>{
  const d=newEngineDraft();checkedDraft(d);assert.throws(()=>engineProfile(d),/absolute path/);
  assert.throws(()=>checkedDraft({...d,command:'execute'}));
  assert.throws(()=>checkedDraft({...d,modelFamily:'<img src=x>'}));
  assert.throws(()=>checkedDraft({...d,sources:[]}));
});
test('replica grammar rejects ambiguous paths and HTML fields remain escaped',()=>{
  const d=split();d.sources[1].path='/model,other.gguf';assert.throws(()=>engineProfile(d),/commas/);
  d.sources[1].path='/model*other.gguf';assert.throws(()=>engineProfile(d),/asterisks/);
  d.sources[0].path='/models/"<img src=x>".gguf';assert(!engineSettingsView(d).includes('<img src=x>'));
});
test('exported environment preserves literal shell metacharacters and clears stale managed flags',()=>{
  const dir=mkdtempSync(join(tmpdir(),'argodrive-env-test-'));
  try{
    const d=split(),marker=join(dir,'must-not-exist');
    d.sources[1].path=`/Volumes/One/a'$(touch ${marker})\`echo unsafe\`.gguf`;
    const p=engineProfile(d),env=join(dir,'candidate.env');writeFileSync(env,engineEnvironment(p));
    const output=execFileSync('/bin/sh',['-c','. "$1"; printf "%s" "$DS4_MODEL_REPLICAS"','check',env],{encoding:'utf8'});
    assert.equal(output,p.environment.DS4_MODEL_REPLICAS);assert(!existsSync(marker));
    assert.match(engineEnvironment(p),/unset DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH/);
  }finally{rmSync(dir,{recursive:true,force:true});}
});

test('V4.1 decode cache reserve is an explicit fork candidate',()=>{
  const d=newEngineDraft();Object.assign(d,{modelFamily:'deepseek41',build:'argonaut',decodeCachePct:'100'});d.sources[0].path='/model';
  const p=engineProfile(d);assert.equal(p.environment.DS4_ARGODRIVE_DECODE_CACHE_PCT,'100');
  assert.match(engineSettingsView(d),/Decode cache reserve/);
  d.build='standard';assert(!('DS4_ARGODRIVE_DECODE_CACHE_PCT' in engineProfile(d).environment));
  for(const value of ['0','101','abc']){d.build='argonaut';d.decodeCachePct=value;assert.throws(()=>engineProfile(d),/Decode cache reserve/);}
});
