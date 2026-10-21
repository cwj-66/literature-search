import { useEffect, useMemo, useRef, useState } from "react";
import { AimOutlined, BankOutlined, ExpandOutlined, FileTextOutlined, MinusOutlined, PlusOutlined, TagOutlined, UserOutlined } from "@ant-design/icons";
import { KIND_LABEL, RELATIONS, type EntityGraph } from "../graphData";
import { scholarLayout, type ScholarNode } from "../scholarGraphLayout";
const ICON = { paper: <FileTextOutlined />, keyword: <TagOutlined />, author: <UserOutlined />, venue: <BankOutlined /> };
const cameras = new Map<string, { x:number; y:number; k:number }>();
export function ExplorerCanvas({ data, selected, onSelect, onSelectEdge, scope, resetKey }: {
  data: EntityGraph; selected: string; onSelect: (id:string)=>void; onSelectEdge?: (id:string)=>void; scope:string; resetKey:number;
}) {
  const box=useRef<HTMLDivElement>(null);
  const layout=useMemo(()=>scholarLayout(data, data.nodes.find(n=>n.kind==="paper")?.id??""),[data]);
  const [size,setSize]=useState({width:900,height:680});
  const [camera,setCamera]=useState(cameras.get(scope)??{x:0,y:0,k:.8});
  const cameraRef=useRef(camera); cameraRef.current=camera;
  const drag=useRef<{id:number;x:number;y:number;cx:number;cy:number;moved:boolean}|null>(null);
  const suppressClick=useRef(false);
  const [hover,setHover]=useState("");
  const fit=()=>{ const k=Math.min(1.1,(size.width-40)/layout.width,(size.height-100)/layout.height);setCamera({x:(size.width-layout.width*k)/2,y:(size.height-layout.height*k)/2+20,k}); };
  useEffect(()=>{const el=box.current;if(!el)return;const ro=new ResizeObserver(()=>setSize({width:el.clientWidth,height:el.clientHeight}));ro.observe(el);return()=>ro.disconnect();},[]);
  useEffect(()=>{fit();},[layout.width,layout.height,size.width,size.height,resetKey,scope]);
  useEffect(()=>()=>{cameras.set(scope,cameraRef.current);},[scope]);
  useEffect(()=>{const el=box.current;if(!el)return;const wheel=(event:WheelEvent)=>{event.preventDefault();const r=el.getBoundingClientRect(),c=cameraRef.current,x=event.clientX-r.left,y=event.clientY-r.top,k=Math.max(.1,Math.min(2.5,c.k*Math.exp(-event.deltaY*.001)));setCamera({x:x-(x-c.x)*k/c.k,y:y-(y-c.y)*k/c.k,k});};el.addEventListener("wheel",wheel,{passive:false});return()=>el.removeEventListener("wheel",wheel);},[]);
  const byId=new Map(layout.nodes.map(n=>[n.node.id,n]));
  const focus=hover||selected;
  const adjacent=new Set([focus,...data.edges.filter(e=>e.source===focus||e.target===focus).flatMap(e=>[e.source,e.target])]);
  const front=new Set([selected,...data.edges.filter(e=>e.source===selected||e.target===selected).flatMap(e=>[e.source,e.target])]);
  const edges=data.edges.map((edge,i)=>({edge,i,lit:Boolean(focus)&&(edge.source===focus||edge.target===focus)}));
  const zoom=(factor:number)=>{const c=camera,k=Math.max(.1,Math.min(2.5,c.k*factor));setCamera({k,x:size.width/2-(size.width/2-c.x)*k/c.k,y:size.height/2-(size.height/2-c.y)*k/c.k});};
  return <div ref={box} className="kg-explorer-canvas scholar-canvas" aria-label="可拖动缩放的科研知识关系网络" onPointerDown={event=>{suppressClick.current=false;if((event.target as Element).closest("button,a"))return;drag.current={id:event.pointerId,x:event.clientX,y:event.clientY,cx:camera.x,cy:camera.y,moved:false};suppressClick.current=false;event.currentTarget.setPointerCapture(event.pointerId);}} onPointerMove={event=>{const d=drag.current;if(!d||event.pointerId!==d.id)return;const dx=event.clientX-d.x,dy=event.clientY-d.y;if(Math.abs(dx)+Math.abs(dy)>5)d.moved=true;if(d.moved)setCamera(c=>({...c,x:d.cx+dx,y:d.cy+dy}));}} onPointerUp={()=>{suppressClick.current=Boolean(drag.current?.moved);drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
    <div className="kg-canvas-tools"><button aria-label="缩小图谱" onClick={()=>zoom(1/1.2)}><MinusOutlined /></button><button aria-label="放大图谱" onClick={()=>zoom(1.2)}><PlusOutlined /></button><button onClick={fit}><ExpandOutlined /> 适应画布</button><button aria-label="定位选中节点" onClick={()=>{const n=byId.get(selected);if(n)setCamera(c=>({...c,x:size.width/2-n.x*c.k,y:size.height/2-n.y*c.k}));else fit();}}><AimOutlined /></button></div>
    <svg className="scholar-network" width={size.width} height={size.height} aria-label={`${layout.nodes.length} 个知识节点`}>
      <defs>{Object.entries(RELATIONS).map(([key,value])=><marker key={key} id={`scholar-${key}`} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7Z" fill={value.color}/></marker>)}</defs>
      <g transform={`translate(${camera.x},${camera.y}) scale(${camera.k})`}>
        {edges.filter(e=>!e.lit).map(edgeEl)}
        {layout.nodes.filter(n=>!front.has(n.node.id)).map(nodeEl)}
        {edges.filter(e=>e.lit).map(edgeEl)}
        {layout.nodes.filter(n=>front.has(n.node.id)).sort((a,b)=>Number(a.node.id===selected)-Number(b.node.id===selected)).map(nodeEl)}
      </g>
    </svg>
    {!data.nodes.length?<p className="scholar-canvas-empty">当前筛选下没有节点，请调整上方显示类型。</p>:null}
  </div>;
  function edgeEl({edge,i,lit}:typeof edges[number]){const a=byId.get(edge.source),b=byId.get(edge.target);if(!a||!b)return null;const dx=b.x-a.x,dy=b.y-a.y,clip=(n:typeof a)=>1/Math.max(Math.abs(dx)/(n.width/2+4),Math.abs(dy)/(n.height/2+4),1),sa=clip(a),sb=clip(b),x0=a.x+dx*sa,y0=a.y+dy*sa,x=b.x-dx*sb,y=b.y-dy*sb,bend=["cites","similar"].includes(edge.relation)?.14:0,mx=(x0+x)/2-dy*bend,my=(y0+y)/2+dx*bend;const meta=RELATIONS[edge.relation],d=`M${x0},${y0} Q${mx},${my} ${x},${y}`;return <g key={`${edge.source}-${edge.target}-${edge.relation}`} className={`scholar-edge${lit?" is-lit":focus?" is-muted":""}`} onClick={()=>{if(!suppressClick.current)onSelectEdge?.(`edge-${i}`);}}>{lit?<path className="scholar-edge-casing" d={d}/>:null}<path d={d} fill="none" stroke={meta?.color??"#bac9de"} strokeWidth={lit?2.4:1} strokeDasharray={meta?.dash?.join(" ")} markerEnd={edge.relation==="cites"?"url(#scholar-cites)":undefined}/><path className="scholar-edge-hit" d={d}/>{lit&&["cites","similar"].includes(edge.relation)?<text className="scholar-edge-label" x={mx} y={my-6} textAnchor="middle" fill={meta?.color} fontSize="11">{edge.relation==="cites"?"引用":"相似"}</text>:null}</g>;}
  function nodeEl({node,x,y,width,height}:ScholarNode){return <g key={node.id}>{node.id===selected?<rect className="scholar-node-halo" pointerEvents="none" x={x-width/2-8} y={y-height/2-8} width={width+16} height={height+16} rx={node.kind==="keyword"?22:18}/>:null}<foreignObject x={x-width/2} y={y-height/2} width={width} height={height} overflow="visible"><button type="button" className={`scholar-node is-${node.kind}${node.id===selected?" is-selected":""}${focus&&!adjacent.has(node.id)&&node.id!==selected?" is-muted":focus&&node.id!==focus&&node.id!==selected?" is-linked":""}`} title={node.label} aria-label={`${KIND_LABEL[node.kind]}：${node.label}`} aria-pressed={node.id===selected} onMouseEnter={()=>setHover(node.id)} onMouseLeave={()=>setHover("")} onFocus={()=>setHover(node.id)} onBlur={()=>setHover("")} onClick={()=>{if(!suppressClick.current)onSelect(node.id);}}><i>{ICON[node.kind]}</i><span><b>{node.label}</b>{node.kind==="paper"?<small>{node.year??"年份未知"}<em>·</em>{node.venue||node.sources?.[0]||"论文"}</small>:null}</span></button></foreignObject></g>;}
}
