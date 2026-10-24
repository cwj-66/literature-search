import { useMemo, useState } from "react";
import { Button, Checkbox, Drawer, Empty, Input, Select } from "antd";
import { BankOutlined, FileTextOutlined, SearchOutlined, ShareAltOutlined, TagOutlined, UserOutlined, ExportOutlined, DoubleLeftOutlined, DoubleRightOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { RELATIONS, type EntityEdge, type EntityGraph, type EntityNode } from "../graphData";
import { useMediaQuery } from "../hooks";
import { canvasGraph, filterGraph, relatedPapers, selectGraphPapers } from "../knowledgeGraph";
import { ExplorerCanvas } from "./ExplorerCanvas";
import { TopicEvolution } from "./TopicEvolution";
const DEFAULT="paper,keyword,cites,similar,author,venue";
const ICON={paper:<FileTextOutlined />,keyword:<TagOutlined />,author:<UserOutlined />,venue:<BankOutlined />};
export function KnowledgeExplorer({data,scope,paperHref,paperKey,workspaceId,recommendHref,references}:{
  data:EntityGraph;scope:string;paperHref:(node:EntityNode)=>string;paperKey?:(node:EntityNode)=>string|undefined;workspaceId?:string;recommendHref?:(node:EntityNode)=>string;
  references?:string[];
}) {
  const narrow=useMediaQuery("(max-width: 900px)");
  const [params,setParams]=useSearchParams();
  const [resetKey,setResetKey]=useState(0),[collapsed,setCollapsed]=useState(false),[edge,setEdge]=useState<EntityEdge|null>(null);
  const query=params.get("q")??"",year=Number(params.get("year"))||0;
  const show=(params.get("show")||DEFAULT).split(",");
  const update=(values:Record<string,string>)=>setParams(current=>{const next=new URLSearchParams(current);Object.entries(values).forEach(([k,v])=>v?next.set(k,v):next.delete(k));return next;},{replace:true});
  const picked = (params.get("papers") || "").split(",").filter(id => data.nodes.some(n => n.kind === "paper" && n.id === id));
  const visible=useMemo(()=>filterGraph(selectGraphPapers(data,picked),query,year,year),[data,picked.join(","),query,year]);
  const selected=visible.nodes.find(n=>n.id===params.get("selected"))??visible.nodes.find(n=>n.kind==="paper");
  const shown=useMemo(()=>{const nodes=visible.nodes.filter(n=>show.includes(n.kind)),ids=new Set(nodes.map(n=>n.id));return {...visible,nodes,edges:visible.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)&&(!["cites","similar"].includes(e.relation)||show.includes(e.relation)))};},[visible,show.join(",")]);
  const graph=useMemo(()=>canvasGraph(shown,Object.keys(RELATIONS),selected?.id??""),[shown,selected?.id]);
  const papers=visible.nodes.filter(n=>n.kind==="paper");
  const select=(id:string)=>{setEdge(null);update({selected:id});};
  function reset(){setEdge(null);setCollapsed(false);update({q:"",year:"",selected:"",show:"",tab:"",papers:""});setResetKey(k=>k+1);}
  function exportGraph(){const blob=new Blob([JSON.stringify(visible,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="knowledge-graph.json";a.click();URL.revokeObjectURL(url);}
  const detail=<ScholarDetail key={edge?`${edge.source}-${edge.target}`:selected?.id} node={selected} edge={edge} data={visible} onSelect={select} paperHref={paperHref} recommendHref={recommendHref} compareHref={workspaceId&&selected&&selected.kind==="paper"?`/w/${workspaceId}/compare/table?pick=${encodeURIComponent(paperKey?.(selected)||selected.local_id||selected.paper_id||selected.id)}`:undefined}/>;
  return <div className="kg-explorer scholar-explorer">
    <div className="scholar-graph-toolbar"><Input prefix={<SearchOutlined />} allowClear placeholder="搜索论文、关键词、作者或期刊 / 会议…" aria-label="搜索图谱" value={query} onChange={e=>{setEdge(null);update({q:e.target.value,selected:""});}}/>
      <Select aria-label="发表年份" value={year||""} onChange={v=>update({year:String(v),selected:""})} options={[{value:"",label:"全部年份"},...[...new Set(data.nodes.filter(n=>n.kind==="paper").flatMap(n=>n.year?[n.year]:[]))].sort((a,b)=>b-a).map(y=>({value:y,label:String(y)}))]}/>
      <Select className="scholar-paper-selector" mode="multiple" allowClear showSearch maxTagCount="responsive" aria-label="选择图谱论文" placeholder="选择论文（默认全部）" value={picked} optionFilterProp="label" options={data.nodes.filter(n=>n.kind==="paper").map(n=>({value:n.id,label:n.label}))} onChange={ids=>{setEdge(null);update({papers:ids.join(","),selected:""});}}/><Button onClick={reset}>复位 ⌄</Button>
    </div>
    <div className="scholar-filter-line"><div className="kg-entity-toggles">{([["paper","论文"],["keyword","关键词"],["cites","引用"],["similar","相似"],["author","作者"],["venue","期刊 / 会议"]] as const).map(([value,label])=><label key={value} className={show.includes(value)?"is-checked":""}><Checkbox checked={show.includes(value)} onChange={e=>update({show:(e.target.checked?[...new Set([...show,value])]:show.filter(v=>v!==value)).join(",")||"none"})}/><span>{value in ICON?ICON[value as keyof typeof ICON]:<ShareAltOutlined />}</span>{label}</label>)}</div>{workspaceId?null:<button className="scholar-text-button" onClick={()=>update({tab:params.get("tab")==="evolution"?"":"evolution"})}>{params.get("tab")==="evolution"?"返回关系图":"主题变化"}</button>}<button className="scholar-text-button" onClick={exportGraph} aria-label="导出图谱数据"><ExportOutlined /></button></div>
    {params.get("tab")==="evolution"?<TopicEvolution references={references} onExploreTheme={t=>update({q:t.keywords[0]??t.name,tab:"",selected:""})}/>:!papers.length?<Empty description="没有符合条件的论文"><Button onClick={reset}>清除筛选</Button></Empty>:<div className={`scholar-graph-workspace${collapsed&&!narrow?" is-detail-collapsed":""}`}>
      <section className="scholar-graph-panel" aria-label="关系网络画布">{!narrow?<button className="scholar-inspector-toggle" aria-label={collapsed?"展开节点详情":"收起节点详情"} aria-expanded={!collapsed} onClick={()=>setCollapsed(v=>!v)}>{collapsed?<DoubleLeftOutlined />:<DoubleRightOutlined />}</button>:null}<ExplorerCanvas data={graph} selected={selected?.id??""} onSelect={select} onSelectEdge={id=>{const e=graph.edges[Number(id.slice(5))];if(e)setEdge(e);}} scope={scope} resetKey={resetKey}/><div className="scholar-legend"><span className="is-paper"><FileTextOutlined />论文</span><span className="is-keyword"><TagOutlined />关键词</span><span className="is-author"><UserOutlined />作者</span><span className="is-venue"><BankOutlined />期刊 / 会议</span><span><i className="is-citation"/>引用关系</span><span><i className="is-similar"/>相似关系</span></div></section>
      {narrow?<><Button className="scholar-open-detail" onClick={()=>update({detail:"1"})}>查看节点详情</Button><Drawer open={params.get("detail")==="1"} onClose={()=>update({detail:""})} placement="bottom" height="76%">{detail}</Drawer></>:!collapsed?<aside className="scholar-detail">{detail}</aside>:null}
    </div>}
  </div>;
}
function ScholarDetail({node,edge,data,onSelect,paperHref,recommendHref,compareHref}:{node?:EntityNode;edge:EntityEdge|null;data:EntityGraph;onSelect:(id:string)=>void;paperHref:(n:EntityNode)=>string;recommendHref?:(n:EntityNode)=>string;compareHref?:string}){
  const [tab,setTab]=useState("basic"),[expanded,setExpanded]=useState(false);
  const nodes=new Map(data.nodes.map(n=>[n.id,n]));
  if(edge)return <><h2 className="scholar-detail-heading">关系详情 <small>Relation Details</small></h2><div className="scholar-detail-inner"><h3>{RELATIONS[edge.relation]?.label??"关联关系"}</h3>{[edge.source,edge.target].map(id=><button className="scholar-related-item" key={id} onClick={()=>onSelect(id)}><FileTextOutlined /><b>{nodes.get(id)?.label??id}</b></button>)}<p>来源：{edge.provenance||"已保存的图谱关系"}</p></div></>;
  if(!node)return <Empty description="选择节点，查看详情"/>;
  const linked=data.edges.filter(e=>e.source===node.id||e.target===node.id);
  const topics=linked.filter(e=>e.relation==="has_keyword").flatMap(e=>{const n=nodes.get(e.source===node.id?e.target:e.source);return n?[n]:[];});
  const related=node.kind==="paper"?relatedPapers(data,node).map(r=>({node:r.node,reason:r.reasons[0]})):linked.flatMap(e=>{const n=nodes.get(e.source===node.id?e.target:e.source);return n?.kind==="paper"?[{node:n,reason:RELATIONS[e.relation]?.label??"关联"}]:[];});
  const citations=linked.filter(e=>e.relation==="cites");
  const list=(items:typeof related)=><div className="scholar-related-list">{items.length?items.map(item=><button className="scholar-related-item" key={item.node.id} onClick={()=>onSelect(item.node.id)}><FileTextOutlined /><span><b>{item.node.label}</b><small>{item.node.year??"年份未知"} · {item.node.venue||"论文"}</small><em>{item.reason}</em></span></button>):<p className="scholar-empty-copy">暂无已保存的关联记录。</p>}</div>;
  return <><h2 className="scholar-detail-heading">节点详情</h2><div className="scholar-detail-inner">
    <div className="scholar-detail-title"><i className={`is-${node.kind}`}>{ICON[node.kind]}</i><h3>{node.label}</h3>{node.year?<span>{node.year}</span>:null}</div>
    <div className="scholar-detail-tabs" role="tablist">{[["basic","基本信息"],["relations","关联关系"],["related","相关论文"],["citations","引文网络"]].map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}</button>)}</div>
    {tab==="basic"?<><dl className="scholar-facts"><dt>作者</dt><dd>{node.authors?.join(", ")||"未提供"}</dd><dt>期刊／会议</dt><dd>{node.venue||"未提供"}</dd><dt>年份</dt><dd>{node.year??"未提供"}</dd><dt>类型</dt><dd>{{paper:"论文",keyword:"关键词",author:"作者",venue:"期刊 / 会议"}[node.kind]}</dd>{node.url&&/^https?:\/\//i.test(node.url)?<><dt>链接</dt><dd><a href={node.url} target="_blank" rel="noopener noreferrer">查看论文原文 ↗</a></dd></>:null}</dl>
      {node.kind==="paper"?<><h4>摘要</h4><p className={`scholar-abstract${expanded?" is-expanded":""}`}>{node.abstract||"这篇论文尚未提供摘要。"}</p>{(node.abstract?.length??0)>180?<button className="scholar-expand" onClick={()=>setExpanded(v=>!v)}>{expanded?"收起 ⌃":"展开 ⌄"}</button>:null}<h4>关键词</h4><div className="scholar-keywords">{topics.length?topics.map(n=><button key={n.id} onClick={()=>onSelect(n.id)}>{n.label}</button>):<span className="scholar-empty-copy">暂无已提取的关键词</span>}</div></>:null}<div className="scholar-detail-subhead"><h4>相关论文</h4><button onClick={()=>setTab("related")}>查看更多 ›</button></div>{list(related.slice(0,3))}
    </>:tab==="related"?list(related):tab==="citations"?list(citations.flatMap(e=>{const n=nodes.get(e.source===node.id?e.target:e.source);return n?[{node:n,reason:e.source===node.id?"当前论文引用":"引用了当前论文"}]:[];})):<div className="scholar-relation-list">{linked.length?linked.map((e,i)=><button key={i} onClick={()=>onSelect(e.source===node.id?e.target:e.source)}><span>{RELATIONS[e.relation]?.label}</span><b>{nodes.get(e.source===node.id?e.target:e.source)?.label}</b></button>):<p>暂无已保存的关系。</p>}</div>}
    {node.kind==="paper"?<div className="scholar-detail-actions"><Link to={paperHref(node)}>阅读与分析 ↗</Link>{recommendHref?<Link to={recommendHref(node)}>相似论文</Link>:null}{compareHref?<Link to={compareHref}>加入对比</Link>:null}</div>:null}
  </div></>;
}
