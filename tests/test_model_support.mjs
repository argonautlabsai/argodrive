import test from 'node:test';
import assert from 'node:assert/strict';
import {supportView,readinessView,profileView} from '../monitor/model-support.js';
import {newEngineDraft,engineProfile,engineSettingsView} from '../monitor/engine-settings.js';
test('V4.1 selects its own capability reference and does not export GLM extensions',()=>{
  const d=newEngineDraft();d.modelFamily='deepseek41';d.sources[0].path='/models/DeepSeek-V4.1-Flash-Q4.gguf';
  const p=engineProfile(d);assert.equal(p.model.engram_disk_only,true);
  assert.equal(p.engine.reference.base,'bd66c402070042bf0a79ad6ece8242de4c93680c');
  assert(p.argv.includes('--think-level'));assert(p.argv.includes('4096'));
  assert(!('DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH' in p.environment));
  assert.equal(p.model.local_qualification,false);
  d.build='argonaut';const fork=engineProfile(d);
  assert.equal(fork.environment.DS4_ARGODRIVE_ENGRAM_READERS,'8');
  assert.equal(fork.environment.DS4_ARGODRIVE_RESIDENT_GATE,'1');
  assert(!Object.keys(fork.environment).some(k=>k.startsWith('DS4_MODEL_')));
  assert.equal(fork.engine.installed_binary_verified,false);
  assert(engineSettingsView(d).includes('DeepSeek V4.1 Flash'));
});
test('V4.1 split export uses its own flags and refuses unsupported planner knobs',()=>{
  const d=newEngineDraft();Object.assign(d,{modelFamily:'deepseek41',build:'argonaut',method:'split',nocache:'on'});
  d.sources=[{path:'/model',weight:'2',pieces:'1',inflight:'0'},{path:'/Green/model',weight:'1',pieces:'1',inflight:'0'},{path:'/White/model',weight:'1',pieces:'1',inflight:'0'}];
  const p=engineProfile(d);assert.equal(p.environment.DS4_ARGODRIVE_REPLICAS,'/Green/model*1,/White/model*1');
  assert.equal(p.environment.DS4_ARGODRIVE_PRIMARY_WEIGHT,'2');assert.equal(p.environment.DS4_ARGODRIVE_PRIMARY_NOCACHE,'1');
  assert(p.unset_environment.includes('DS4_MODEL_REPLICAS'));assert.equal(p.status,'unvalidated');
  d.sources[0].pieces='2';assert.throws(()=>engineProfile(d),/not implemented/);d.sources[0].pieces='1';
  d.method='hashed';assert.throws(()=>engineProfile(d),/single or split/);d.method='split';
  d.sources.push({...d.sources[1],path:'/fourth/model'});assert.throws(()=>engineProfile(d),/at most three/);
});
test('model and readiness strings are escaped; unknown traffic is not rendered as zero',()=>{
  const html=supportView({models:[{engine:'ds4',label:'<script>',status:'Experimental',description:'<img src=x>'}]});
  assert(!html.includes('<script>'));assert(!html.includes('<img src=x>'));
  const r=readinessView({size_and_header_match:false,file_bytes:null,expected_bytes:518596067328,
    placements:[{directory:'<img src=x>',free_bytes:null,additional_bytes_needed:null,error:'<script>'}],checks:[],notes:[]});
  assert(!r.includes('<script>'));assert(!r.includes('<img src=x>'));assert(r.includes('—'));
});
test('Kimi card exposes the Deltafin multi-drive method as a candidate',()=>{
  const html=supportView({models:[{id:'kimi3',engine:'Deltafin',label:'Kimi K3',status:'Recorded-run support',description:'K3',
    streaming_method:'Complete replicas with weighted split-read',streaming_reference:{base_commit:'abc123',scope:'candidate',
      drive_ladder:{one_drive_fraction_of_four:.52,two_drive_fraction_of_four:.73,three_drive_fraction_of_four:.90},
      recommended_environment:{K3_SPLIT_READ:'2',K3_TIER_BALANCE:'1'}}}]});
  assert(html.includes('Complete replicas with weighted split-read'));
  assert(html.includes('K3_SPLIT_READ'));
  assert(html.includes('52%'));
  assert(html.includes('Candidate only'));
});
test('experimental engine profile remains unapplied and escapes exported values',()=>{
  const html=profileView({status:'Experimental',engine:'fork',expert_policy:'Split',engram_policy:'Primary',
    cache_policy:'Automatic',errors:['<script>'],environment:{DS4_ARGODRIVE_REPLICAS:'<img src=x>'}});
  assert(html.includes('Not applied'));assert(html.includes('Starting settings'));
  assert(!html.includes('<script>'));assert(!html.includes('<img src=x>'));
});
