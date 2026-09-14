import assert from 'node:assert/strict';
import {test} from 'node:test';
import {spotlightPanel,spotlightConfirmation} from '../monitor/spotlight-view.js';

test('all five drives appear; indexing controls are scoped and escaped',()=>{
  const volumes=Array.from({length:5},(_,i)=>({id:'d'+i,label:i===4?'<Blue>':'SSD '+i,path:'/Volumes/SSD '+i,
    can_change:i<4,state:i===4?'unavailable':i===2?'disabled':'enabled',evidence:'<macOS>'}));
  const html=spotlightPanel({volumes,checked_at:1});
  assert.match(html,/5 volumes/);assert.match(html,/3 with indexing on/);
  assert.match(html,/&lt;Blue&gt;/);assert.doesNotMatch(html,/<Blue>/);
  assert.equal((html.match(/data-spotlight-volume=/g)||[]).length,4);
  assert.match(html,/data-spotlight-enabled="true"/);assert.match(html,/Status unavailable/);
});
test('pending authentication disables controls; unknown is not off',()=>{
  const html=spotlightPanel({volumes:[{id:'x',label:'SSD',path:'/v',state:'enabled',can_change:true}],job:{status:'running',message:'Authentication'}});
  assert.match(html,/data-spotlight-enabled="false" disabled/);
  assert.match(html,/Waiting for macOS/);assert.doesNotMatch(spotlightPanel(null),/Indexing off/);
});
test('confirmation explains persistence, search effects, and startup volume',()=>{
  const text=spotlightConfirmation({path:'/',label:'Macintosh HD',system:true},false);
  assert.match(text,/startup volume/);assert.match(text,/persists/);assert.match(text,/search results may stop updating/);
});
