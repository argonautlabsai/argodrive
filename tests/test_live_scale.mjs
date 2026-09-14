import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {sharedReadBarScale,monitorReadBarScale,readAutoScale,readWindowStats,finite,fmt} from '../monitor/app-model.js';
const devices=[{id:'internal'},{id:'Green'}];
test('live cards use one stable axis and grow together above it',()=>{
  assert.deepEqual(sharedReadBarScale(devices,{internal:[[100,8]],Green:[[99,4]]}),{end:100,ceiling:16});
  assert.equal(sharedReadBarScale(devices,{internal:[[100,18]],Green:[[100,4]]}).ceiling,20);
  assert.equal(sharedReadBarScale(devices,{internal:[[100,0]],Green:[[100,0]]}).ceiling,16);
  assert.equal(sharedReadBarScale(devices,{internal:[[1,100],[200,4]],Green:[[200,2]]}).ceiling,16);
});
test('real card SVG preserves the 8 versus 4 GB/s height ratio and aligned timestamps',()=>{
  const source=fs.readFileSync(new URL('../monitor/app.js',import.meta.url),'utf8');
  const fn=source.slice(source.indexOf('function driveReadBars('),source.indexOf('\nfunction ssdSeries'));
  const ctx={finite,fmt,readWindowStats};vm.createContext(ctx);vm.runInContext(fn,ctx);
  const scale=sharedReadBarScale(devices,{internal:[[100,8]],Green:[[100,4]]});
  const a=ctx.driveReadBars([[100,8,.1]],'blue',scale),b=ctx.driveReadBars([[100,4,.1]],'green',scale);
  const height=s=>Number(s.match(/<rect[^>]* height="([^"]+)"/)[1]);
  assert.equal(height(a),height(b)*2);
  assert.match(a,/shared scale zero to 16 gigabytes per second/);assert.match(b,/shared scale zero to 16 gigabytes per second/);
  assert.doesNotMatch(ctx.driveReadBars([[60,8,.1]],'blue',scale),/<rect/);
});
test('detailed chart computes weighted mean and separates delayed intervals from burst peaks',()=>{
 const source=fs.readFileSync(new URL('../monitor/app.js',import.meta.url),'utf8');
 const fn=source.slice(source.indexOf('function driveReadBars('),source.indexOf('\nfunction ssdSeries'));
 const ctx={finite,fmt,readWindowStats};vm.createContext(ctx);vm.runInContext(fn,ctx);
 const html=ctx.driveReadBars([[99,8,.1],[99.9,0,.9],[100,4,.1]],'blue',{end:100,ceiling:16},20);
 assert.match(html,/Average <strong>1\.09 <small>GB\/s/);
 assert.match(html,/Peak <strong>8\.00 <small>GB\/s/);
 assert.match(html,/class="live-average-line"/);assert.match(html,/class="live-peak-line"/);
 assert.match(html,/100 ms interval/);
 for(const seconds of [10,20,60]){
  const h=ctx.driveReadBars([[75,4,.1]],'blue',{end:100,ceiling:16},seconds);
  assert.equal(h.includes('<rect'),seconds===60);
  assert.ok(h.includes(`last ${seconds} seconds`));
 }
});

test('combined chart uses simultaneous total intervals and its own visible-window scale',()=>{
 const source=fs.readFileSync(new URL('../monitor/app.js',import.meta.url),'utf8');
 const fn=source.slice(source.indexOf('function driveReadBars('),source.indexOf('\nfunction ssdSeries'));
 const ctx={finite,fmt,readWindowStats};vm.createContext(ctx);vm.runInContext(fn,ctx);
 const html=ctx.combinedReadCard([[99,8,.1],[100,7,.1]],{end:100,ceiling:16},20,3);
 assert.match(html,/Combined read throughput/);assert.match(html,/aggregate scale zero to 12/);
 assert.match(html,/Peak <strong>8\.00/);assert.match(html,/Average <strong>7\.50/);
 assert.doesNotMatch(ctx.combinedReadCard([],{end:100,ceiling:16},20,3),/<rect/);
});

test('Monitor uses a fixed 0–16 GB/s per-drive axis',()=>{
 const drives=[{id:'internal'},{id:'Green'}],traces={internal:[[1,30,.1],[100,9.5,.1]],Green:[[100,4.5,.1]]};
 const shared=monitorReadBarScale(drives,traces,20);
 assert.deepEqual(shared,{end:100,ceiling:16,mode:'shared'});
 assert.equal(monitorReadBarScale([drives[1]],traces,20,shared.end).ceiling,16);
 assert.equal(monitorReadBarScale(drives,{},20).ceiling,16);
 assert.equal(monitorReadBarScale(drives,{internal:[[100,200,.1]]},20).ceiling,16);
 // The lower-level autoscaler remains available for other chart types.
 assert.equal(readAutoScale(drives,traces,20).ceiling,12);
});
