import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {topologyLayout,topologySVG,nodeReadMetrics,topologyInspector,topologyWire,topologyScale} from '../monitor/topology-view.js';

const inventory={nodes:[
  {id:'mac',kind:'mac',label:'Mac',parent:null,drive_ids:[]},
  {id:'port',kind:'port',label:'Port 1',parent:'mac',connected:true,drive_ids:[]},
  {id:'hub',kind:'hub',label:'TB5 hub',parent:'port',drive_ids:[]},
  {id:'a',kind:'enclosure',label:'<SSD A>',parent:'hub',drive_ids:['a']},
  {id:'b',kind:'enclosure',label:'SSD B',parent:'hub',drive_ids:['b']},
  {id:'empty',kind:'port',label:'Port 2',parent:'mac',connected:false,drive_ids:[]}
],edges:[['mac','port'],['port','hub'],['hub','a'],['hub','b'],['mac','empty']].map(([source,target])=>({id:source+'-'+target,source,target}))};
test('hub branches stay separate and empty-port filter keeps valid edges',()=>{
  const l=topologyLayout(inventory,false);
  assert(!l.placed.has('empty'));
  assert.equal(l.edges.length,4);
  assert.equal(l.placed.get('a').parent,'hub');
  assert.equal(l.placed.get('b').parent,'hub');
  assert.notEqual(l.placed.get('a').y,l.placed.get('b').y);
  const svg=topologySVG(inventory,{},{});
  assert(svg.includes('&lt;SSD A&gt;'));
  assert(!svg.includes('<SSD A>'));
});
test('hub rate requires aligned, present, measured child intervals',()=>{
  const l=topologyLayout(inventory),n=l.placed.get('hub');
  const d={health:{scope:true},devices:[{id:'a',present:true},{id:'b',present:true}],cur:{a:5,b:4},
    read_windows:{a:[[10,5,.2]],b:[[10,4,.2]]}};
  assert.equal(nodeReadMetrics(n,d,l).rate,9);
  d.read_windows.b=[[10.1,4,.2]];
  assert.equal(nodeReadMetrics(n,d,l).rate,null);
  d.devices[1].present=false;
  assert.equal(nodeReadMetrics(n,d,l).rate,null);
});
test('idle and stale metrics do not become zero latency or stale request size',()=>{
  const l=topologyLayout(inventory),n=l.placed.get('a');
  const d={health:{scope:true},devices:[{id:'a',present:true}],cur:{a:0},read_windows:{a:[[10,0,.2]]},
    read_operations:{a:{operations:0,mean_read_ms:null,read_iops:0,mean_read_kib:null}}};
  assert.equal(nodeReadMetrics(n,d,l).latency,null);
  assert.equal(nodeReadMetrics(n,d,l).iops,0);
  d.health.scope=false;d.read_operations.a={operations:10,mean_read_ms:1,read_iops:2,mean_read_kib:512};
  assert.equal(nodeReadMetrics(n,d,l).requestKiB,null);
});
test('shared hub is labelled without inventing a combined SSD ceiling',()=>{
  const topo={...inventory,shared_uplinks:[{node_id:'hub',label:'TB5 hub',drive_ids:['a','b'],reported_link:'80 Gb/s',calibrated_gbps:null}]};
  assert.match(topologySVG(topo,{}),/Shared uplink · 2 SSDs/);
  const html=topologyInspector(topo,{devices:[{id:'a',label:'Yellow'},{id:'b',label:'Blue'}]},{selected:'a'});
  assert.match(html,/Yellow \+ Blue/);assert.match(html,/shared read limit not calibrated/);
  assert.match(html,/Individual peaks cannot be added/);
  assert.match(topologyInspector(topo,{}, {selected:'hub'}),/Shared bandwidth/);
});

const fiveDrive=JSON.parse(readFileSync(new URL('./fixtures/topology-five-drive.json',import.meta.url)));
function assertGeometry(layout){
  const host=layout.placed.get('mac');
  assert.equal(host.x+host.w/2,layout.width/2,'host is horizontally centred');
  assert.equal(host.y,layout.height/2,'host is vertically centred');
  for(const a of layout.nodes){
    assert(a.x>=28&&a.x+a.w<=layout.width-28,`${a.id} inside horizontal bounds`);
    assert(a.y-a.h/2>=28&&a.y+a.h/2<=layout.height-28,`${a.id} inside vertical bounds`);
    for(const b of layout.nodes){
      if(a.id===b.id)continue;
      const overlap=a.x<b.x+b.w&&a.x+a.w>b.x&&a.y-a.h/2<b.y+b.h/2&&a.y+a.h/2>b.y-b.h/2;
      assert(!overlap,`${a.id} overlaps ${b.id}`);
    }
  }
  const onBoundary=(n,x,y)=>x>=n.x&&x<=n.x+n.w&&y>=n.y-n.h/2&&y<=n.y+n.h/2&&
    (x===n.x||x===n.x+n.w||y===n.y-n.h/2||y===n.y+n.h/2);
  for(const e of layout.edges){
    const a=layout.placed.get(e.source),b=layout.placed.get(e.target),wire=topologyWire(a,b);
    assert(onBoundary(a,wire.x1,wire.y1),`${e.id} docks at source`);
    assert(onBoundary(b,wire.x2,wire.y2),`${e.id} docks at destination`);
    assert(!/NaN|Infinity|undefined/.test(wire.path));
  }
}
test('five drives fit a compact centred map with the hub and both SSDs visible',()=>{
  const layout=topologyLayout(fiveDrive);
  assert.equal(layout.nodes.length,10);assert.equal(layout.edges.length,9);
  assertGeometry(layout);
  assert(layout.width<=1200&&layout.height<=800,'five-drive fit should keep cards readable');
  assert(layout.placed.get('Blue').x<layout.placed.get('hub').x);
  assert(layout.placed.get('Yellow').x>layout.placed.get('hub').x);
  assert.equal(layout.placed.get('Blue').y,layout.placed.get('Yellow').y);
  const reversed=topologyLayout({...fiveDrive,nodes:[...fiveDrive.nodes].reverse()});
  assert.deepEqual([...layout.placed], [...reversed.placed],'enumeration order must not shuffle the map');
});
test('nested hubs, many drives, empty ports and unassociated storage stay separate',()=>{
  for(const count of [1,2,3,5,8,12]){
    const topo=structuredClone(fiveDrive);
    for(let i=0;i<count;i++){
      const parent=i%2?'hub':'nested';
      topo.nodes.push({id:`extra-${i}`,kind:'enclosure',label:`Extra ${i}`,parent,drive_ids:[]});
      topo.edges.push({id:`wire-${i}`,source:parent,target:`extra-${i}`});
    }
    topo.nodes.push({id:'nested',kind:'hub',label:'Nested hub',parent:'port3',drive_ids:[]});
    topo.edges.push({id:'nested-wire',source:'port3',target:'nested'});
    topo.nodes.push({id:'unassociated',kind:'ssd',label:'Unknown enclosure',unresolved:true,parent:'missing',drive_ids:[]});
    topo.nodes.push({id:'spare',kind:'port',label:'Spare port',connected:false,parent:'mac',drive_ids:[]});
    topo.edges.push({id:'spare-wire',source:'mac',target:'spare'});
    const layout=topologyLayout(topo);
    assert.equal(layout.nodes.length,topo.nodes.length);
    assertGeometry(layout);
    assertGeometry(topologyLayout(topo,false));
    assert(!topologyLayout(topo,false).placed.has('spare'));
  }
});
test('Fit respects both dimensions and numeric zoom retains native card size',()=>{
  const layout=topologyLayout(fiveDrive);
  for(const [width,height] of [[925,540],[360,440],[1100,720]]){
    const scale=topologyScale(layout,width,height);
    assert(layout.width*scale<=width+.001);
    assert(layout.height*scale<=height+.001);
  }
  assert.equal(topologyScale(layout,360,440,100),1);
  assert.equal(topologyScale(layout,1100,720,150),1.5);
  assert.match(topologySVG(fiveDrive,{}),/preserveAspectRatio="xMidYMid meet"/);
  assert.match(topologySVG(fiveDrive,{}, {zoom:'fit'}),/width:100%;height:100%/);
  assert.match(topologySVG(fiveDrive,{}, {zoom:150}),new RegExp(`width:${layout.width*1.5}px;height:${layout.height*1.5}px`));
});
test('missing root and cyclic parent data cannot hang inspection or lose all nodes',()=>{
  const cycle={nodes:[{id:'mac',kind:'mac',label:'Mac',parent:null},
    {id:'a',kind:'hub',label:'A',parent:'b'},{id:'b',kind:'hub',label:'B',parent:'a'}],edges:[]};
  assert.equal(topologyLayout(cycle).nodes.length,3);
  assertGeometry(topologyLayout(cycle));
  assert.match(topologyInspector(cycle,{}, {selected:'a'}),/Connection path/);
  assert.equal(topologyLayout({nodes:[],edges:[]}).nodes.length,0);
});
