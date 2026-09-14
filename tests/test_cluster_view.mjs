import test from 'node:test';
import assert from 'node:assert/strict';
import {clusterView} from '../monitor/cluster-view.js';

test('missing evidence is an unqualified recorded state',()=>{
  const html=clusterView({});
  assert.match(html,/Remote inference not enabled/);
  assert.match(html,/Awaiting independent link/);
  assert.doesNotMatch(html,/Transfer identity check passed/);
});
test('correctness does not turn a report into live performance qualification',()=>{
  const html=clusterView({source:'bundled_reference',s1:{verified:1000,requested:1000,identity_pass:true,transfer_gbs:1.3},network:{connected_links:1}});
  assert.match(html,/Recorded test session/);assert.match(html,/bundled reference/);
  assert.match(html,/Transfer identity check passed/);assert.match(html,/1.30/);
  assert.equal((html.match(/cluster-link observed/g)||[]).length,1);
  assert.equal((html.match(/cluster-link planned/g)||[]).length,2);
});
test('external evidence text is escaped',()=>{
  const html=clusterView({node:{name:'<img src=x onerror=alert(1)>'},gates:[{title:'<script>bad</script>',detail:'a & b'}]});
  assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;img/);assert.match(html,/a &amp; b/);
});

test('measured bandwidth is separated from expert and inference performance',()=>{
  const html=clusterView({network:{connected_links:2,independent_links:2},
    network_test:{duration_seconds:5,cases:[{name:'AB-repeat',label:'Both cables',wall_gbs:9.34,streams:'4 + 4',headline:true}]},
    expert_test:{cases:[{name:'AB-repeat',verified:2000,requested:2000,wall_gbs:2.71,identity_pass:true}]}});
  assert.match(html,/Measured cable bandwidth/);assert.match(html,/9.34/);
  assert.match(html,/Real expert transfers/);assert.match(html,/2.710/);
  assert.match(html,/no SSD reads or model execution/);
  assert.match(html,/Remote inference not enabled/);
  assert.equal((html.match(/independent path tested/g)||[]).length,2);
});

test('shared cache evidence stays distinct from a live inference pool',()=>{
  const html=clusterView({pipeline_test:{cache_implemented:true,identity_pass:true,verified:25000,requested:25000,servers_stopped:true,
    profiles:[{name:'Warm cache',headline:true,transfer_gbs:9.35,wall_gbs:3.86,cache_hit_rate:1,gain_percent:45,repetitions:2}]}});
  assert.match(html,/Queued transfers \+ shared RAM cache/);
  assert.match(html,/9.35/);assert.match(html,/3.860/);
  assert.match(html,/no remote cache currently allocated/);
  assert.match(html,/Remote inference not enabled/);
  assert.doesNotMatch(html,/RAM pool: not implemented/);
});

test('an inference regression is shown without promoting the integration',()=>{
  const html=clusterView({inference_test:{local_tok_s:1.958,remote_tok_s:1.773,change_percent:-9.4,identity_pass:true,
    verdict:'Slower; keep experimental',cache_hit_rate:1,remote_bytes:38319685632,experts:192,servers_stopped:true,
    cases:[{tag:'<unsafe>',tokens:128,decode_tok_s:1.773,identity_pass:true}]}});
  assert.match(html,/GLM inference/);assert.match(html,/-9.4%/);assert.match(html,/not promoted/);
  assert.match(html,/Experimental engine tested · default off/);assert.match(html,/not the full-model cache hit rate/);
  assert.match(html,/1.958/);assert.match(html,/1.773/);assert.doesNotMatch(html,/<unsafe>/);
  assert.doesNotMatch(html,/Metal wrapping and engine fallback are pending/);
});
