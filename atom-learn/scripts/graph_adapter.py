#!/usr/bin/env python3
"""Optional dependency-free interactive renderer for graph-view-v1 payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atomlearn import atomic_text


def render_interactive_graph(view: dict[str, Any], output_path: Path) -> Path:
    """Write a standalone HTML adapter without changing canonical AtomLearn state."""
    payload = json.dumps(view, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("&", "\\u0026")
    html = TEMPLATE.replace("__GRAPH_VIEW_JSON__", payload)
    atomic_text(output_path, html)
    return output_path


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AtomLearn Knowledge Graph</title>
<style>
:root{color-scheme:light;--bg:#f5f7f8;--surface:#fff;--ink:#172126;--muted:#607079;--line:#cbd5da;--accent:#176b55;--focus:#b45309;--paper:#27628a;--optional:#805d14}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0}
header{height:64px;display:flex;align-items:center;gap:16px;padding:0 20px;background:var(--surface);border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0;white-space:nowrap}.revision{color:var(--muted);font-size:12px}.controls{margin-left:auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
input[type=search]{width:min(280px,30vw);height:36px;border:1px solid #9eabb1;padding:0 10px;background:#fff;color:var(--ink)}
button{height:36px;border:1px solid #809097;background:#fff;color:var(--ink);padding:0 12px;cursor:pointer}button:hover{border-color:var(--accent)}
label{display:inline-flex;align-items:center;gap:5px;color:#34434a;white-space:nowrap}
main{display:grid;grid-template-columns:minmax(0,1fr) 300px;height:calc(100vh - 64px)}
.viewport{position:relative;overflow:auto;background-image:linear-gradient(#dfe5e8 1px,transparent 1px),linear-gradient(90deg,#dfe5e8 1px,transparent 1px);background-size:24px 24px}
#surface{position:relative;min-width:100%;min-height:100%}svg{position:absolute;inset:0;overflow:visible;pointer-events:none}.node{position:absolute;width:210px;height:72px;padding:9px 11px;border:1px solid #8d9ba1;border-left:4px solid var(--accent);background:var(--surface);text-align:left;overflow:hidden;cursor:pointer}
.node.paper{border-left-color:var(--paper)}.node.optional{border-left-color:var(--optional);border-style:dashed}.node.focus{outline:3px solid #f0b45b;outline-offset:2px}.node.selected{box-shadow:0 0 0 3px #176b5533}.node.dim{opacity:.24}
.node strong{display:block;font-size:13px;line-height:1.25;max-height:33px;overflow:hidden}.node small{display:block;margin-top:5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
aside{border-left:1px solid var(--line);background:var(--surface);padding:18px;overflow:auto}aside h2{font-size:15px;margin:0 0 12px}aside dl{margin:0}dt{font-size:11px;color:var(--muted);text-transform:uppercase;margin-top:14px}dd{margin:2px 0;overflow-wrap:anywhere}
.legend{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:20px}.key{font-size:11px;color:var(--muted)}.swatch{display:inline-block;width:18px;height:3px;margin-right:5px;vertical-align:middle}
.empty{padding:32px;color:var(--muted)}@media(max-width:800px){header{height:auto;min-height:72px;align-items:flex-start;padding:12px;flex-wrap:wrap}.controls{margin-left:0;width:100%}input[type=search]{width:100%}main{height:calc(100vh - 118px);grid-template-columns:1fr}aside{display:none}}
</style>
</head>
<body>
<header><div><h1>AtomLearn Knowledge Graph</h1><div class="revision" id="revision"></div></div><div class="controls"><input id="search" type="search" aria-label="Search nodes" placeholder="Search concepts or papers"><label><input class="edge-filter" type="checkbox" value="prerequisite" checked>Prerequisite</label><label><input class="edge-filter" type="checkbox" value="containment" checked>Containment</label><label><input class="edge-filter" type="checkbox" value="semantic-related" checked>Related</label><label><input class="edge-filter" type="checkbox" value="citation" checked>Citation</label><button id="focus-button">Current focus</button><button id="reset-button">Reset</button></div></header>
<main><section class="viewport" id="viewport" aria-label="Interactive knowledge graph"><div id="surface"><svg id="edges" aria-hidden="true"></svg><div id="nodes"></div></div></section><aside><h2>Selection</h2><div id="details" class="empty">Select a node to inspect its status and connections.</div><div class="legend"><div class="key"><span class="swatch" style="background:#176b55"></span>Atom</div><div class="key"><span class="swatch" style="background:#27628a"></span>Paper</div><div class="key"><span class="swatch" style="background:#805d14"></span>Optional</div><div class="key"><span class="swatch" style="background:#b45309"></span>Focus</div></div></aside></main>
<script id="graph-data" type="application/json">__GRAPH_VIEW_JSON__</script>
<script>
const graph=JSON.parse(document.getElementById('graph-data').textContent);const nodeById=new Map(graph.nodes.map(n=>[n.id,n]));const surface=document.getElementById('surface');const nodeLayer=document.getElementById('nodes');const svg=document.getElementById('edges');const viewport=document.getElementById('viewport');let selected=null;
document.getElementById('revision').textContent=`graph ${graph.revision} · course ${graph.course_revision} · ${graph.nodes.length} nodes`;
const colors={'prerequisite':'#176b55','containment':'#805d14','scheduled-successor':'#9a5a2a','optional-branch':'#805d14','citation':'#27628a','semantic-related':'#6b5f75'};
function visibleEdges(){const checked=new Set([...document.querySelectorAll('.edge-filter:checked')].map(e=>e.value));return graph.edges.filter(e=>checked.has(e.kind)||!['prerequisite','containment','semantic-related','citation'].includes(e.kind));}
function layout(){const atomIds=graph.nodes.filter(n=>n.kind==='atom').map(n=>n.id);const depth=new Map(atomIds.map(id=>[id,0]));for(let pass=0;pass<atomIds.length;pass++)for(const e of graph.edges)if(e.kind==='prerequisite'&&depth.has(e.from)&&depth.has(e.to))depth.set(e.to,Math.max(depth.get(e.to),depth.get(e.from)+1));let maxDepth=Math.max(0,...depth.values());for(const n of graph.nodes)if(n.kind==='paper')depth.set(n.id,++maxDepth);const groups=new Map();for(const n of graph.nodes){const d=depth.get(n.id)||0;if(!groups.has(d))groups.set(d,[]);groups.get(d).push(n)}let maxRows=1;for(const [d,nodes] of groups){nodes.sort((a,b)=>(a.module||'').localeCompare(b.module||'')||a.id.localeCompare(b.id));maxRows=Math.max(maxRows,nodes.length);nodes.forEach((n,i)=>{n.x=32+d*258;n.y=32+i*96})}surface.style.width=`${Math.max(viewport.clientWidth,80+(maxDepth+1)*258)}px`;surface.style.height=`${Math.max(viewport.clientHeight,80+maxRows*96)}px`}
function renderNodes(){nodeLayer.replaceChildren();for(const n of graph.nodes){const el=document.createElement('button');el.className=`node ${n.kind} ${n.optional?'optional':''} ${n.focus?'focus':''}`;el.dataset.id=n.id;el.style.left=`${n.x}px`;el.style.top=`${n.y}px`;const title=document.createElement('strong');title.textContent=n.label;const meta=document.createElement('small');meta.textContent=`${n.status} · ${n.module||n.kind}`;el.append(title,meta);el.addEventListener('click',()=>selectNode(n.id));nodeLayer.appendChild(el)}}
function renderEdges(){const active=visibleEdges();svg.setAttribute('width',surface.scrollWidth);svg.setAttribute('height',surface.scrollHeight);svg.replaceChildren();for(const e of active){const a=nodeById.get(e.from),b=nodeById.get(e.to);if(!a||!b)continue;const line=document.createElementNS('http://www.w3.org/2000/svg','path');const x1=a.x+210,y1=a.y+36,x2=b.x,y2=b.y+36,mid=(x1+x2)/2;line.setAttribute('d',`M${x1} ${y1} C${mid} ${y1},${mid} ${y2},${x2} ${y2}`);line.setAttribute('fill','none');line.setAttribute('stroke',colors[e.kind]||'#7b878c');line.setAttribute('stroke-width',e.kind==='prerequisite'?'2.5':'1.5');if(['optional-branch','scheduled-successor','semantic-related'].includes(e.kind))line.setAttribute('stroke-dasharray','6 5');line.setAttribute('opacity','.72');svg.appendChild(line)}}
function selectNode(id){selected=id;const neighbors=new Set([id]);for(const e of graph.edges)if(e.from===id||e.to===id){neighbors.add(e.from);neighbors.add(e.to)}for(const el of document.querySelectorAll('.node')){el.classList.toggle('selected',el.dataset.id===id);el.classList.toggle('dim',!neighbors.has(el.dataset.id))}const n=nodeById.get(id);const connections=graph.edges.filter(e=>e.from===id||e.to===id);const box=document.getElementById('details');box.className='';box.replaceChildren();const dl=document.createElement('dl');for(const [label,value] of [['Label',n.label],['ID',n.id],['Kind',n.kind],['Status',n.status],['Module',n.module||'None'],['Connections',String(connections.length)]]){const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;dl.append(dt,dd)}box.appendChild(dl)}
function applySearch(){const q=document.getElementById('search').value.trim().toLowerCase();for(const el of document.querySelectorAll('.node')){const n=nodeById.get(el.dataset.id);el.hidden=!!q&&!`${n.label} ${n.id} ${n.module||''}`.toLowerCase().includes(q)}renderEdges()}
function goFocus(){const n=graph.nodes.find(n=>n.focus);if(!n)return;selectNode(n.id);viewport.scrollTo({left:Math.max(0,n.x-viewport.clientWidth/2),top:Math.max(0,n.y-viewport.clientHeight/2),behavior:'smooth'})}
document.getElementById('search').addEventListener('input',applySearch);document.querySelectorAll('.edge-filter').forEach(e=>e.addEventListener('change',renderEdges));document.getElementById('focus-button').addEventListener('click',goFocus);document.getElementById('reset-button').addEventListener('click',()=>{selected=null;document.getElementById('search').value='';document.querySelectorAll('.node').forEach(e=>{e.hidden=false;e.classList.remove('selected','dim')});renderEdges()});window.addEventListener('resize',()=>{layout();renderNodes();renderEdges()});layout();renderNodes();renderEdges();
</script>
</body>
</html>
"""
