import test from 'node:test';
import assert from 'node:assert/strict';
import {engramMonitorView} from '../monitor/engram-monitor-view.js';

const data={health:{scope:true},devices:[{id:'internal',label:'Internal',present:true,connection:'Apple Fabric',device:'disk0',ceiling_gbps:13.5}],read_windows:{internal:[[10,4,1]],TOTAL:[[10,4,1]]}};

test('Engram monitor keeps unclassified physical reads honest',()=>{
  const html=engramMonitorView(data,{window:20});
  assert.match(html,/Monitor|Attribution unavailable|Unclassified device reads/);
  assert.match(html,/primary SSD/);
  assert.doesNotMatch(html,/Engram rows <b>4/);
});

test('Engram monitor renders coloured class overlay when engine telemetry exists',()=>{
  const classified={...data,streaming_attribution:{status:'available',source:'Engine request telemetry',devices:{internal:{weights:[[10,3,1]],engram:[[10,1,1]]}}}};
  const html=engramMonitorView(classified,{window:20});
  assert.match(html,/Classified engine counters/);
  assert.match(html,/Weight tensors/);
  assert.match(html,/Engram rows/);
  assert.match(html,/var\(--purple\)/);
  assert.match(html,/var\(--teal\)/);
});
