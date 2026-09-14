import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {finite,fmt,readWindowStats,sharedReadBarScale,monitorReadBarScale,readAutoScale,escapeHTML} from '../monitor/app-model.js';
const source=fs.readFileSync(new URL('../monitor/app.js',import.meta.url),'utf8');
function harnessContext(api){
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:''});return nodes.get(id);};
 const ctx={state:{route:'monitor',monitorSource:'latest',monitorEpoch:0,monitorCursor:null,liveWindow:20,monitorShowSSD:true,monitorShowEngram:false,monitorCombined:true},api,$,document:{activeElement:null},finite,fmt,readWindowStats,sharedReadBarScale,monitorReadBarScale,readAutoScale,esc:escapeHTML,devColor:()=> 'blue',unit:(v,u)=>`${fmt(v)} ${u}`,badge:v=>v,metric:(n,v)=>`${n}:${v}`,panel:(n,s,b)=>b,empty:(n,b)=>n+b,linkButton:()=>'',driveReadBars:()=>'<svg></svg>',combinedReadCard:()=>'<section>Combined</section>'};
 vm.createContext(ctx);
 vm.runInContext(source.slice(source.indexOf("let harnessSelected=''"),source.indexOf('\nsetInterval(',source.indexOf("let harnessSelected=''"))),ctx);
 return {ctx,nodes};
}
test('a pending test response cannot overwrite hardware source after switching',async()=>{
 let finish;const {ctx,nodes}=harnessContext(()=>new Promise(r=>finish=r));
 const pending=ctx.loadHarness();ctx.state.monitorSource='hardware';ctx.state.monitorEpoch++;
 finish({arm:'stale',id:'stale.log'});await pending;
 assert.equal(nodes.has('#harness-content'),false);
});
test('hardware source makes no harness request',async()=>{
 let calls=0;const {ctx}=harnessContext(async()=>{calls++;return {};});ctx.state.monitorSource='hardware';await ctx.loadHarness();assert.equal(calls,0);
});
test('past source pins an explicit recording instead of following a later latest test',async()=>{
 const paths=[];const {ctx}=harnessContext(async p=>{paths.push(p);return {arm:'A',id:'day/A.log',choices:[],read_windows:{},devices:[]};});
 ctx.state.monitorSource='past';await ctx.loadHarness();await ctx.loadHarness();assert.deepEqual(paths,['/harness','/harness?arm=day%2FA.log']);
});
test('scrubbing changes chart statistics while totals remain labelled as final engine timing',()=>{
 const {ctx,nodes}=harnessContext(async()=>({}));ctx.state.monitorSource='past';ctx.state.monitorCursor=10;ctx.state.liveWindow=10;
 ctx.updateHarness({arm:'A',id:'A',done:true,seconds:30,summary:{ds4_gen_tps:3.4},choices:[],devices:[{id:'internal',label:'Internal'}],read_windows:{internal:[[5,8,.2],[25,4,.2]],TOTAL:[[5,8,.2],[25,4,.2]]}});
 const html=nodes.get('#harness-content').innerHTML;assert.match(html,/Host SSD average:8\.00/);assert.match(html,/Engine decode:3\.40/);assert.match(html,/Recorded position/);assert.match(html,/last up to 120 seconds/);
});
test('legacy monitor links resolve to one canonical route with the correct source',()=>{
 const fn=source.slice(source.indexOf('function navigate(){'),source.indexOf("\nwindow.addEventListener('hashchange'"));
 for(const [hash,mode] of [['#live','hardware'],['#runmonitor','latest'],['#monitor','latest']]){
  const ctx={location:{hash},state:{monitorSource:'latest'},names:{monitor:'Monitor',overview:'Overview'},history:{replaceState(){}},render(){}};vm.createContext(ctx);vm.runInContext(fn,ctx);ctx.navigate();assert.equal(ctx.state.route,'monitor');assert.equal(ctx.state.monitorSource,mode);
 }
});

test('Monitor Engram is a first-class route and refreshes from the shared data payload',()=>{
 assert.match(source,/engram-monitor-view\.js/);
 assert.match(source,/engram:'Monitor Engram'/);
 assert.match(source,/engram:renderEngram/);
 assert.match(source,/\['monitor','engram','ssds','topology'\]/);
});

test('combined checkbox preference hides only the combined chart',()=>{
 const {ctx,nodes}=harnessContext(async()=>({}));
 const record={arm:'A',done:true,seconds:10,summary:{ds4_gen_tps:3.4},choices:[],devices:[{id:'internal',label:'Internal'}],read_windows:{internal:[[10,8,.1]],TOTAL:[[10,8,.1]]}};
 ctx.state.monitorCombined=false;ctx.updateHarness(record);
 assert.doesNotMatch(nodes.get('#harness-content').innerHTML,/<section>Combined<\/section>/);
 assert.match(nodes.get('#harness-content').innerHTML,/Host SSD average:8\.00/);
 ctx.state.monitorCombined=true;ctx.updateHarness(record);
 assert.match(nodes.get('#harness-content').innerHTML,/<section>Combined<\/section>/);
});

test('inference stages group encode with prefill and do not animate stale or historical stages',()=>{
 const {ctx}=harnessContext(async()=>({}));
 const d={chunks:3,stage:{current:'prefill',live:true,seen:['setup','prefill'],progress:{processed:128,total:512}}};
 let html=ctx.monitorStages(d);assert.match(html,/Prefill \/ encode/);assert.match(html,/128 \/ 512 input tokens/);assert.match(html,/aria-current="step"/);
 ctx.state.monitorSource='past';html=ctx.monitorStages(d);assert.doesNotMatch(html,/aria-current="step"/);assert.match(html,/Whole-run stages/);
 ctx.state.monitorSource='latest';d.stage.live=false;html=ctx.monitorStages(d);assert.doesNotMatch(html,/aria-current="step"/);assert.match(html,/Last reported:/);
});
