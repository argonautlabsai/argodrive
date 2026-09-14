import {escapeHTML as esc,finite,fmt,readWindowStats} from './app-model.js';

const tone=n=>(n.drive_ids||[]).includes('Green')?'#348e7b':(n.drive_ids||[]).includes('White')?'#c78a3c':(n.drive_ids||[]).includes('Yellow')?'#996188':(n.drive_ids||[]).includes('Blue')?'#526be0':n.kind==='mac'?'#6854d9':'#4284b6';
const trim=(s,n=29)=>String(s||'').length>n?String(s).slice(0,n-1)+'…':String(s||'');
const kindLabel={mac:'HOST',port:'THUNDERBOLT / USB4',hub:'HUB / DOCK',enclosure:'NVMe ENCLOSURE',ssd:'SSD',device:'PERIPHERAL'};
const pictograms={
  mac:'<rect x="5" y="4" width="54" height="35" rx="3"/><path d="M1 44h62l-5 6H6z M25 44h14"/><rect x="27" y="16" width="11" height="11" rx="2"/>',
  hub:'<rect x="3" y="15" width="58" height="28" rx="7"/><path d="M12 24h8v7h-8z M28 24h8v7h-8z M44 24h8v7h-8z M32 15V4 M27 4h10"/>',
  enclosure:'<rect x="10" y="4" width="44" height="46" rx="7"/><path d="M18 13h28 M18 18h28 M18 23h28 M18 28h28 M18 33h28 M28 44h8"/>',
  ssd:'<rect x="6" y="12" width="52" height="31" rx="4"/><path d="M13 19h15v16H13z M35 19h15v16H35z M14 43v6 M22 43v6 M30 43v6 M38 43v6 M46 43v6"/>',
  port:'<rect x="6" y="14" width="52" height="28" rx="14"/><path d="M18 25h28v6H18z"/>',
  device:'<rect x="9" y="7" width="46" height="36" rx="5"/><path d="M24 49h16 M32 43v6 M19 20h26 M19 29h16"/>'
};
function glyph(kind,x,y,scale=1){return `<g transform="translate(${x} ${y}) scale(${scale})" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${pictograms[kind]||pictograms.device}</g>`;}

// A four-sided tree keeps the host central without implying physical socket positions.
// Each branch reserves its full cross-axis span before any nodes are placed.
export function topologyLayout(inventory,showEmpty=true){
  const source=(inventory.nodes||[]).filter(n=>showEmpty||n.kind!=='port'||n.connected);
  if(!source.length)return {nodes:[],edges:[],placed:new Map(),width:640,height:480};
  const root=source.find(n=>n.id==='mac')||source[0], ids=new Set(source.map(n=>n.id));
  const children=new Map(source.map(n=>[n.id,[]]));
  for(const n of source)if(n.id!==root.id&&ids.has(n.parent))children.get(n.parent).push(n);
  for(const list of children.values())list.sort((a,b)=>a.id.localeCompare(b.id));
  const size=n=>n.kind==='port'?{w:128,h:52}:n.kind==='mac'?{w:248,h:148}:{w:224,h:140};
  const seen=new Set([root.id]), forest=[];
  function tree(n){
    if(seen.has(n.id))return null;
    seen.add(n.id);
    const descendants=(children.get(n.id)||[]).map(tree).filter(Boolean);
    return {node:n,...size(n),children:descendants,count:1+descendants.reduce((sum,c)=>sum+c.count,0)};
  }
  for(const n of children.get(root.id)||[]) {const t=tree(n);if(t)forest.push(t);}
  // Keep unassociated devices visible without inventing an edge to the host.
  for(const n of source)if(!seen.has(n.id)){const t=tree(n);if(t)forest.push(t);}
  const zones={top:[],left:[],right:[],bottom:[]}, loads={top:0,left:0,right:0,bottom:0};
  const internal=forest.filter(t=>t.node.kind==='ssd'&&!t.node.unresolved);
  const external=forest.filter(t=>!internal.includes(t)).sort((a,b)=>b.count-a.count||a.node.id.localeCompare(b.node.id));
  // The largest hub branch gets the space above the host; direct drives flank it.
  for(const t of external){
    const order=external.length===1?['right','left','top','bottom']:external.length===2?['left','right','top','bottom']:['top','left','right','bottom'];
    const zone=order.reduce((best,z)=>loads[z]<loads[best]?z:best,order[0]);
    zones[zone].push(t);loads[zone]+=t.count;
  }
  for(const t of internal)zones.bottom.push(t);
  const host={...root,...size(root),x:-size(root).w/2,y:0,depth:0,zone:'host'};
  const gap=26, branchGap=32, groups={};
  for(const [zone,trees] of Object.entries(zones)){
    const vertical=zone==='top'||zone==='bottom', sign=zone==='left'||zone==='top'?-1:1, levels=[];
    function measure(t,depth=0){
      t.cross=vertical?t.w:t.h;t.along=vertical?t.h:t.w;
      t.fan=t.node.kind==='hub'&&t.children.length>0&&t.children.length<=2&&t.children.every(c=>!c.children.length);
      if(t.fan){
        const childCross=Math.max(...t.children.map(c=>vertical?c.w:c.h));
        levels[depth]=Math.max(levels[depth]||0,t.along,...t.children.map(c=>vertical?c.h:c.w));
        t.span=t.cross+2*(gap+childCross);
        return t.span;
      }
      levels[depth]=Math.max(levels[depth]||0,t.along);
      t.span=Math.max(t.cross,t.children.reduce((sum,c)=>sum+measure(c,depth+1),0)+Math.max(0,t.children.length-1)*branchGap);
      return t.span;
    }
    trees.forEach(t=>measure(t));
    const distances=[];
    for(let d=0;d<levels.length;d++)distances[d]=d?distances[d-1]+levels[d-1]/2+gap+levels[d]/2:(vertical?host.h:host.w)/2+gap+levels[d]/2;
    const nodes=[];
    function place(t,cross,depth){
      const along=distances[depth]*sign, cx=vertical?cross:along,cy=vertical?along:cross;
      nodes.push({...t.node,w:t.w,h:t.h,x:cx-t.w/2,y:cy,depth:depth+1,zone});
      // Fan terminal SSDs beside a small hub instead of adding another long row.
      if(t.fan){
        t.children.forEach((child,i)=>{
          const offset=(i===0?-1:1)*(t.cross/2+gap+(vertical?child.w:child.h)/2);
          nodes.push({...child.node,w:child.w,h:child.h,x:(vertical?cross+offset:along)-child.w/2,y:vertical?along:cross+offset,depth:depth+2,zone,wireSide:vertical?(i===0?'left':'right'):(i===0?'top':'bottom')});
        });
        return;
      }
      const span=t.children.reduce((sum,c)=>sum+c.span,0)+Math.max(0,t.children.length-1)*branchGap;
      let cursor=cross-span/2;
      for(const child of t.children){place(child,cursor+child.span/2,depth+1);cursor+=child.span+branchGap;}
    }
    const span=trees.reduce((sum,t)=>sum+t.span,0)+Math.max(0,trees.length-1)*branchGap;
    let cursor=-span/2;
    for(const t of trees){place(t,cursor+t.span/2,0);cursor+=t.span+branchGap;}
    groups[zone]=nodes;
  }
  // Large/nested hubs can extend into a neighbouring quadrant. Move whole branches
  // outward until their reserved rectangles are separate; topology stays unchanged.
  const overlaps=(a,b)=>a.x<b.x+b.w+18&&a.x+a.w+18>b.x&&a.y-a.h/2<b.y+b.h/2+18&&a.y+a.h/2+18>b.y-b.h/2;
  const directions=Object.keys(groups);
  for(let iteration=0;iteration<source.length*40;iteration++){
    const move=new Set();
    for(let i=0;i<directions.length;i++)for(let j=i+1;j<directions.length;j++){
      const a=directions[i],b=directions[j];
      if(groups[a].some(n=>groups[b].some(m=>overlaps(n,m)))){move.add(a);move.add(b);}
    }
    if(!move.size)break;
    for(const zone of move)for(const n of groups[zone]){
      if(zone==='left')n.x-=26;else if(zone==='right')n.x+=26;else n.y+=zone==='top'?-26:26;
    }
  }
  const nodes=[host,...Object.values(groups).flat()];
  const halfWidth=Math.max(320,...nodes.map(n=>Math.max(Math.abs(n.x),Math.abs(n.x+n.w))+28));
  const halfHeight=Math.max(240,...nodes.map(n=>Math.abs(n.y)+n.h/2+28));
  for(const n of nodes){n.x+=halfWidth;n.y+=halfHeight;}
  const placed=new Map(nodes.map(n=>[n.id,n]));
  return {nodes,edges:(inventory.edges||[]).filter(e=>placed.has(e.source)&&placed.has(e.target)),placed,width:halfWidth*2,height:halfHeight*2};
}

export function topologyWire(a,b){
  const side=b.wireSide||b.zone,vertical=side==='top'||side==='bottom', reverse=side==='top'||side==='left';
  const x1=vertical?a.x+a.w/2:a.x+(reverse?0:a.w),y1=vertical?a.y+(reverse?-a.h/2:a.h/2):a.y;
  const x2=vertical?b.x+b.w/2:b.x+(reverse?b.w:0),y2=vertical?b.y+(reverse?b.h/2:-b.h/2):b.y;
  const middle=vertical?(y1+y2)/2:(x1+x2)/2;
  const path=vertical?`M${x1} ${y1} C${x1} ${middle} ${x2} ${middle} ${x2} ${y2}`:`M${x1} ${y1} C${middle} ${y1} ${middle} ${y2} ${x2} ${y2}`;
  return {x1,y1,x2,y2,path};
}

// Fit uses both viewport dimensions. Numeric zoom is a percentage of native SVG
// units, so 100% always means the same card size, regardless of window width.
export function topologyScale(layout,width,height,zoom='fit'){
  return zoom==='fit'?Math.min(width/layout.width,height/layout.height):Number(zoom)/100;
}

export function nodeReadMetrics(node,data,layout){
  const descendants=new Set(),seen=new Set();
  function collect(n){if(seen.has(n.id))return;seen.add(n.id);for(const id of n.drive_ids||[])descendants.add(id);for(const c of layout.nodes.filter(c=>c.parent===n.id))collect(c);}
  collect(node);
  const ids=[...descendants], devices=new Map((data.devices||[]).map(d=>[d.id,d]));
  const mapped=ids.length&&ids.every(id=>devices.get(id)?.present);
  const fresh=mapped&&data.health?.scope;
  const currents=ids.map(id=>data.cur?.[id]);
  const rate=fresh&&currents.every(finite)?currents.reduce((a,b)=>a+b,0):null;
  const stats=ids.map(id=>data.read_operations?.[id]);
  const operations=stats.every(Boolean)?stats.reduce((s,x)=>s+x.operations,0):0;
  const latency=operations&&stats.every(s=>!s.operations||finite(s.mean_read_ms))?stats.reduce((s,x)=>s+(x.mean_read_ms||0)*x.operations,0)/operations:null;
  // Parent throughput sums only simultaneous current device intervals.
  const ends=ids.map(id=>(data.read_windows?.[id]||[]).at(-1));
  const aligned=ends.length&&ends.every(x=>x&&Math.abs(x[0]-ends[0][0])<.001&&Math.abs(x[2]-ends[0][2])<.001);
  const iops=fresh&&stats.length&&stats.every(Boolean)?stats.reduce((s,x)=>s+x.read_iops,0):null;
  const size=operations?stats.reduce((s,x)=>s+(x.mean_read_kib||0)*x.operations,0)/operations:null;
  return {ids,rate:aligned?rate:null,latency:fresh?latency:null,iops,requestKiB:fresh?size:null,
    average:ids.length===1?readWindowStats(data.read_windows?.[ids[0]]||[],30).mean:null,
    peak:ids.length===1?data.peaks?.[ids[0]]:null};
}

export function topologySVG(inventory,data,options={}){
  const layout=topologyLayout(inventory,options.showEmpty!==false), selected=options.selected||'mac';
  const metrics=new Map(layout.nodes.map(n=>[n.id,nodeReadMetrics(n,data,layout)]));
  const edgeHTML=layout.edges.map(e=>{
    const a=layout.placed.get(e.source),b=layout.placed.get(e.target);
    const {x1,y1,x2,y2,path}=topologyWire(a,b);
    const m=metrics.get(b.id), active=finite(m.rate)&&m.rate>.001, unknown=e.kind==='unresolved', color=unknown?'var(--muted)':tone(b);
    // Cable speed labels live on the destination node, keeping branch crossings clean.
    return `<g class="topo-wire ${unknown||e.status==='empty'?'uncertain':''}"><path d="${path}" style="stroke:${color}"/><circle cx="${x1}" cy="${y1}" r="4" fill="var(--surface)" stroke="${color}"/><circle cx="${x2}" cy="${y2}" r="4" fill="var(--surface)" stroke="${color}"/>${active?`<path class="topo-flow" d="${path}" style="stroke:${color}"/>`:''}<title>${esc(a.label+' → '+b.label)} · ${esc(e.speed_label||e.status)} · ${fmt(m.rate)} GB/s measured reads</title></g>`;
  }).join('');
  const nodeHTML=layout.nodes.map(n=>{
    const m=metrics.get(n.id), y=n.y-n.h/2, color=tone(n), e=layout.edges.find(e=>e.target===n.id);
    const driveLabel=(n.storage||[]).map(s=>s.label).join(' + '), title=driveLabel&&n.kind==='enclosure'?driveLabel:n.label;
    const subtitle=n.kind==='mac'?[n.model,n.memory].filter(Boolean).join(' · '):n.kind==='port'?(n.connected?'Connected':'Available'):n.kind==='enclosure'?n.label:n.model||n.protocol||'';
    const shared=(inventory.shared_uplinks||[]).find(g=>g.node_id===n.id);
    const status=shared?`Shared uplink · ${shared.drive_ids.length} SSDs`:n.unresolved?'Association unknown':n.kind==='port'&&!n.connected?'No device':e?.speed_label|| (n.kind==='ssd'&&!n.unresolved?'Built-in storage':n.protocol||'Local host');
    const compact=n.kind==='port', host=n.kind==='mac';
    const name=host?(n.model||title):title;
    const detail=host?[title,n.memory].filter(Boolean).join(' · '):subtitle;
    return `<g class="topo-node ${host?'topo-host':''} ${selected===n.id?'selected':''} ${n.unresolved?'unresolved':''}" role="button" tabindex="0" data-topology-node="${esc(n.id)}" aria-label="Inspect ${esc(title)}" transform="translate(${n.x} ${y})" style="color:${color}">
      <rect class="topo-node-bg" width="${n.w}" height="${n.h}" rx="${compact?16:14}"/>
      ${compact?'':`<rect width="3" height="31" x="0" y="19" rx="1.5" fill="${color}"/>`}
      ${glyph(n.kind,compact?9:12,compact?13:15,compact?.42:.64)}
      ${compact?`<text class="topo-kind" x="44" y="20">${n.connected?'PORT':'AVAILABLE'}</text><text class="topo-port-title" x="44" y="37">${esc(trim(title,12))}</text>`:`
      <text class="topo-kind" x="62" y="26">${kindLabel[n.kind]||'DEVICE'}</text>
      <text class="topo-title" x="62" y="46">${esc(trim(name,host?22:19))}</text>
      <text class="topo-subtitle" x="14" y="72">${esc(trim(detail,host?37:32))}</text>
      <text class="topo-link-speed" x="14" y="91">${esc(trim(status,34))}</text>
      <line x1="14" x2="${n.w-14}" y1="103" y2="103" class="topo-divider"/>
      <text class="topo-kind" x="14" y="125">READS</text>
      <text class="topo-rate" x="${n.w-14}" y="126" text-anchor="end">${fmt(m.rate)} <tspan class="topo-rate-unit">GB/s</tspan></text>`}
      <title>${esc(title+' · '+subtitle+' · '+status)}</title>
    </g>`;
  }).join('');
  const zoom=options.zoom??'fit',scale=topologyScale(layout,layout.width,layout.height,zoom);
  const sizing=zoom==='fit'?'width:100%;height:100%':`width:${layout.width*scale}px;height:${layout.height*scale}px`;
  return `<svg xmlns="http://www.w3.org/2000/svg" class="topology-svg" viewBox="0 0 ${layout.width} ${layout.height}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Mac and Thunderbolt storage connection map" style="${sizing}">${edgeHTML}${nodeHTML}</svg>`;

}

export function topologyInspector(inventory,data,options={}){
  const layout=topologyLayout(inventory,options.showEmpty!==false),node=layout.placed.get(options.selected)||layout.placed.get('mac');
  if(!node)return '';
  const m=nodeReadMetrics(node,data,layout),edge=layout.edges.find(e=>e.target===node.id);
  const shared=(inventory.shared_uplinks||[]).filter(g=>g.node_id===node.id||(node.drive_ids||[]).some(id=>g.drive_ids.includes(id)));
  const driveName=id=>(data.devices||[]).find(d=>d.id===id)?.label||id;
  const row=(name,value)=>`<div class="setting-line"><span>${name}</span><strong>${value}</strong></div>`;
  const chain=[];let current=node;
  const ancestors=new Set();
  while(current&&!ancestors.has(current.id)){ancestors.add(current.id);chain.unshift(current.label);current=layout.placed.get(current.parent);}
  return `<div class="topo-inspector-head"><span class="eyebrow">${kindLabel[node.kind]||'DEVICE'}</span><h2>${esc((node.storage||[]).map(s=>s.label).join(' + ')||node.label)}</h2><p>${esc(node.label)}</p></div>
    <div class="topo-metric"><span>Read throughput</span><strong>${fmt(m.rate)} <small>GB/s</small></strong><p>${m.ids.length>1?'Aligned sum of mapped drives':'Physical-device read counters'}</p></div>
    ${row('Average read time',finite(m.latency)?fmt(m.latency,3)+' ms':'—')}
    ${row('Read operations',finite(m.iops)?fmt(m.iops,0)+' /s':'—')}
    ${row('Mean request',finite(m.requestKiB)?fmt(m.requestKiB,1)+' KiB':'—')}
    ${row('30 s average',finite(m.average)?fmt(m.average)+' GB/s':'—')}
    ${row('Session peak',finite(m.peak)?fmt(m.peak)+' GB/s':'—')}
    ${row('Reported link',esc(edge?.speed_label|| (node.kind==='mac'?'Host':edge?.kind==='internal'?'Built-in':'Not reported')))}
    ${row('Cable latency','Not measured')}
    <p class="chart-note">Read time is the driver’s completed-read average over the last 5 seconds. Idle or unavailable measurements show —.</p>
    ${shared.length?`<div class="topo-inspector-section"><h3>Shared bandwidth</h3>${shared.map(g=>`<p><strong>${esc(g.label)}</strong><br>${esc(g.drive_ids.map(driveName).join(' + '))}<br>${esc(g.reported_link||'Link rate not reported')} · shared read limit ${finite(g.calibrated_gbps)?fmt(g.calibrated_gbps)+' GB/s':'not calibrated'}</p>`).join('')}<p class="chart-note">Test these SSDs together before assigning streaming weights. Individual peaks cannot be added to obtain the shared limit. Nested port and hub links are not separate bandwidth pools.</p></div>`:''}
    <div class="topo-inspector-section"><h3>Connection path</h3><p>${esc(chain.join(' → '))}</p>${node.unresolved?'<p class="data-error">The enclosure association is unresolved; the dashed line does not establish a physical cable.</p>':''}</div>
    ${(node.storage||[]).length?`<div class="topo-inspector-section"><h3>Storage</h3>${node.storage.map(s=>`<p><strong>${esc(s.model||s.label)}</strong><br>${esc(s.device)} ${s.path?'· '+esc(s.path):''}</p>`).join('')}<p class="chart-note">${esc(node.mapping||'')}</p></div>`:''}
    ${(node.ports||[]).length?`<div class="topo-inspector-section"><h3>Connection points</h3>${node.ports.map(p=>row(esc(p.label),esc(p.connected?p.speed||'Connected':'Available'))).join('')}</div>`:''}`;
}

export function topologyConnections(inventory,data,options={}){
  const layout=topologyLayout(inventory,options.showEmpty!==false);
  return `<div class="table-scroll"><table class="tight-table"><thead><tr><th>Connection</th><th>Link reported</th><th>Reads GB/s</th><th>Storage read ms</th><th>Evidence</th></tr></thead><tbody>${layout.edges.map(e=>{const a=layout.placed.get(e.source),b=layout.placed.get(e.target),m=nodeReadMetrics(b,data,layout);return `<tr><td><button class="run-name" data-topology-node="${esc(b.id)}">${esc(a.label)} → ${esc((b.storage||[]).map(s=>s.label).join(' + ')||b.label)}</button></td><td>${esc(e.speed_label||e.status)}</td><td>${fmt(m.rate)}</td><td>${fmt(m.latency,3)}</td><td>${esc(e.kind==='unresolved'?'Association unknown':b.mapping||b.source)}</td></tr>`;}).join('')}</tbody></table></div>`;
}
