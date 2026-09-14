import test from 'node:test';
import assert from 'node:assert/strict';
import {campaignView,campaignExport,selectedCampaign} from '../monitor/campaign-view.js';
const fixture=()=>({id:'argodrive-autotune-fixture',status:'complete',completed_arms:34,candidate:'C5',recorded_qualification_passed:true,target_5_met:false,
  groups:[128,512].map(n=>({tokens:n,three_pairs_complete:true,control:{median:4.},candidate:{median:n===128?4.8:4.2},median_gain_pct:5.,worst_pair_gain_pct:3.})),
  tuning:[{name:'cache-70GB',gain_pct:2.,retained:true}],stages:[{name:'06-qualification',status:'complete',completed:12,planned:12}],
  settings:{environment:{DS4_MODEL_PRIMARY_WEIGHT:'10'}},engine_sha256:['a'.repeat(64)]});
test('qualified target gap is calculated from the slower length',()=>{
  const html=campaignView({campaigns:[fixture()]});
  assert.match(html,/19\.0%/);assert.match(html,/Below 5 tok\/s target/);assert.match(html,/3 matched pairs/);
  assert.match(html,/actual generated tokens/);assert.doesNotMatch(html,/onclick=|onerror=/);
});
test('running and incomplete results cannot become a qualified target',()=>{
  const c=fixture();c.recorded_qualification_passed=false;c.status='running';c.target_5_met=true;c.groups[0].three_pairs_complete=false;
  const html=campaignView({campaigns:[c]});assert.match(html,/Qualification pending/);assert.doesNotMatch(html,/5 tok\/s measured at both lengths/);
  assert.match(html,/Incomplete comparison/);assert.match(html,/More valid pairs/);
});
test('campaign selection does not silently replace an explicitly selected record',()=>{
  const one=fixture(),two={...fixture(),id:'argodrive-refine-second'};
  assert.equal(selectedCampaign({campaigns:[two,one]},one.id),one);
  assert.equal(selectedCampaign({campaigns:[two,one]},'missing'),two);
});
test('saved metadata and settings are escaped before rendering',()=>{
  const c=fixture();c.id='<img src=x onerror=alert(1)>';c.error='<script>unsafe</script>';c.settings.environment.DS4_MODEL_REPLICAS='<b>model</b>';
  const html=campaignView({campaigns:[c]});assert.doesNotMatch(html,/<img|<script>|<b>model/);assert.match(html,/&lt;script&gt;/);
  const exported=JSON.parse(campaignExport(c));assert.equal(exported.campaign.settings.environment.DS4_MODEL_REPLICAS,'<b>model</b>');
  assert.match(exported.note,/not an executable script/);
});
test('empty source offers folder selection without a benchmark launch',()=>{
  const html=campaignView({campaigns:[],errors:[]});assert.match(html,/Choose run folder/);assert.match(html,/does not start a benchmark/);
  assert.doesNotMatch(html,/Start benchmark|Apply settings/);
});
