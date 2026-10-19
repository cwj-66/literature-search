import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import { Drawer, Empty } from "antd";
import { AimOutlined, MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import type { Paper, RecommendationItem, Recommendations } from "../types";
import { paperLocation, workspacePaperPath } from "../workspace";

const CHANNELS = [
  { key: "citation", label: "引用关联", color: "#7362d4", light: "#f2effd", note: "引用 · 共同参考 · 共被引", x: 205, y: 50 },
  { key: "semantic", label: "主题相似", color: "#3984c8", light: "#edf6ff", note: "标题与摘要的语义相似度", x: 1295, y: 50 },
  { key: "keyword", label: "关键词关联", color: "#248e83", light: "#eaf7f3", note: "共同关键词与样本权重", x: 750, y: 657 },
] as const;
type Channel = typeof CHANNELS[number];
type Selection = { item: RecommendationItem; channel: Channel };

function lines(text: string, limit = 28, count = 2) {
  const tokens = text.match(/[\u3400-\u9fff]|[^\s\u3400-\u9fff]+|\s+/gu) ?? [];
  const result: string[] = [];
  let line = ""; let size = 0;
  let truncated = false;
  for (const token of tokens) {
    const width = [...token].reduce((sum, char) => sum + (/[^\u0000-\u00ff]/.test(char) ? 1.8 : 1), 0);
    if (line && size + width > limit) {
      result.push(line.trim()); line = ""; size = 0;
      if (result.length === count) { truncated = true; break; }
    }
    if (!line && !token.trim()) continue;
    line += token; size += width;
  }
  if (result.length < count && line) result.push(line.trim());
  if (truncated) result[count - 1] = result[count - 1].trimEnd() + "…";
  return result;
}

function position(category: string, index: number) {
  return category === "keyword" ? { x: 250 + index * 250, y: 785 }
    : { x: category === "citation" ? 205 : 1295, y: 155 + index * 105 };
}

export function PaperRelationMap({ data, paper, from, workspaceId, centerId }: { data: Recommendations; paper: Paper; from: string; workspaceId: string; centerId: string }) {
  const navigate = useNavigate();
  const prefix = useId().replaceAll(":", "");
  const [selected, setSelected] = useState<Selection | null>(null);
  const [focus, setFocus] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number; ratio: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (element && window.innerWidth <= 600) element.scrollLeft = (element.scrollWidth - element.clientWidth) / 2;
  }, []);
  const groups = CHANNELS.map(channel => ({ channel, group: data.groups?.find(group => group.key === channel.key) }));
  const total = groups.reduce((sum, { group }) => sum + Math.min(5, group?.items.length ?? 0), 0);
  const unique = new Set(groups.flatMap(({ group }) => group?.items.slice(0, 5).map(item => item.id) ?? [])).size;
  const reset = () => { setPan({ x: 0, y: 0 }); setZoom(1); };
  function startDrag(event: PointerEvent<SVGSVGElement>) {
    if (event.pointerType === "touch" && window.innerWidth <= 600) return;
    if ((event.target as Element).closest('[role="button"]') || event.button !== 0) return;
    const bounds = svg.current!.getBoundingClientRect();
    drag.current = { x: event.clientX, y: event.clientY, px: pan.x, py: pan.y, ratio: Math.max(1500 / bounds.width, 900 / bounds.height) / zoom };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function moveDrag(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current) return;
    const d = drag.current;
    setPan({ x: d.px + (event.clientX - d.x) * d.ratio, y: d.py + (event.clientY - d.y) * d.ratio });
  }
  return <section className="relation-map" aria-label="论文关系网络">
    <header className="relation-map-head"><div><span className="map-live-dot" /><strong>关系网络</strong><span className="map-count">{unique} 篇相关文献 · {total} 条关联</span></div>
      <div className="map-legend" aria-label="突出显示关系类别">
        {CHANNELS.map(channel => <button key={channel.key} aria-pressed={focus === channel.key} onClick={() => setFocus(current => current === channel.key ? "" : channel.key)} style={{ color: channel.color }}><i style={{ background: channel.color }} />{channel.label}<span>{Math.min(5, data.groups?.find(g => g.key === channel.key)?.items.length ?? 0)}</span></button>)}
      </div>
    </header>
    <div className="relation-map-canvas" ref={canvas}>
      <div className="map-watermark">FOLLOW THE CONNECTIONS</div>
      <svg ref={svg} className="paper-network-svg" viewBox="0 0 1500 900" aria-label="以当前论文为中心的三类关系网络，可拖动和缩放" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        <defs>
          <radialGradient id={`${prefix}-halo`}><stop offset="0" stopColor="#dfdcfb" stopOpacity=".72" /><stop offset="1" stopColor="#f8f7ff" stopOpacity="0" /></radialGradient>
          <linearGradient id={`${prefix}-center`} x1="0" x2="1" y2="1"><stop stopColor="#6151c5" /><stop offset="1" stopColor="#393487" /></linearGradient>
          <filter id={`${prefix}-shadow`} x="-30%" y="-50%" width="160%" height="220%"><feDropShadow dx="0" dy="5" stdDeviation="9" floodColor="#344078" floodOpacity=".07" /></filter>
        </defs>
        <g transform={`translate(750 430) scale(${zoom}) translate(-750 -430) translate(${pan.x} ${pan.y})`}>
          <circle cx="750" cy="390" r="295" fill={`url(#${prefix}-halo)`} />
          <circle className="map-orbit" cx="750" cy="390" r="183" /><circle className="map-orbit outer" cx="750" cy="390" r="228" />
          {groups.map(({ channel, group }) => <g key={`edges-${channel.key}`} opacity={focus && focus !== channel.key ? .12 : 1}>
            {(group?.items.slice(0, 5) ?? []).map((item, index) => {
              const p = position(channel.key, index);
              const d = channel.key === "keyword" ? `M 750 440 C 750 610, ${p.x} 610, ${p.x} ${p.y - 44}`
                : `M ${channel.key === "citation" ? 610 : 890} 390 C ${channel.key === "citation" ? 440 : 1060} 390, ${channel.key === "citation" ? 480 : 1020} ${p.y}, ${p.x + (channel.key === "citation" ? 116 : -116)} ${p.y}`;
              return <path key={item.id} className="map-connection" d={d} fill="none" stroke={channel.color} strokeOpacity={.25 + item.strength / 240} strokeWidth={1.3 + item.strength / 45} strokeDasharray={channel.key === "semantic" ? "7 6" : channel.key === "keyword" ? "2 6" : undefined} />;
            })}
          </g>)}
          {groups.map(({ channel, group }) => <g key={channel.key} opacity={focus && focus !== channel.key ? .25 : 1}>
            <g transform={`translate(${channel.x},${channel.y})`} className="map-branch-label">
              <text textAnchor="middle" fill={channel.color} fontSize="18" fontWeight="700">{channel.label}<tspan dx="9" fontSize="13" fontWeight="500">{Math.min(5, group?.items.length ?? 0)} / 5</tspan></text>
              <text y="24" textAnchor="middle" fill="#8c94a6" fontSize="12">{channel.note}</text>
            </g>
            {!group?.items.length ? <g transform={`translate(${channel.x},${channel.key === "keyword" ? 765 : 220})`}><text textAnchor="middle" fontSize="14" fill="#9099ac">{group?.status === "unavailable" ? "本类数据暂不可用" : "暂未发现相关文献"}</text><text y="24" textAnchor="middle" fontSize="12" fill="#9099ac">可稍后更新关系</text></g> : null}
            {group?.items.slice(0, 5).map((item, index) => {
              const p = position(channel.key, index);
              return <g key={item.id} transform={`translate(${p.x - 116},${p.y - 44})`} role="button" tabIndex={0} aria-label={`${channel.label}：${item.title}，关联强度 ${item.strength}`} className="map-paper-node" onClick={() => setSelected({ item, channel })} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected({ item, channel }); } }}>
                <title>{item.title}</title><rect className="map-node-shadow" width="232" height="88" rx="13" fill="white" stroke={channel.color} strokeOpacity=".22" filter={`url(#${prefix}-shadow)`} />
                <rect x="0" y="24" width="3" height="40" rx="1.5" fill={channel.color} />
                <text x="15" y="21" fill="#8d96a8" fontSize="11">{String(index + 1).padStart(2, "0")}<tspan dx="10">{item.year ?? "年份未知"}</tspan></text>
                <rect x="173" y="9" width="46" height="19" rx="9" fill={channel.light} /><text x="196" y="22" textAnchor="middle" fill={channel.color} fontSize="11" fontWeight="600">{item.strength}</text>
                <text x="15" y="47" fill="#34405a" fontSize="12.5" fontWeight="550">{lines(item.title, 29).map((line, i) => <tspan key={i} x="15" dy={i ? 18 : 0}>{line}</tspan>)}</text>
              </g>;
            })}
          </g>)}
          <g className="map-center-node" transform="translate(610,316)" role="button" tabIndex={0} aria-label={`查看中心论文：${paper.title}`} onClick={() => navigate(workspacePaperPath(workspaceId, centerId, from))} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(workspacePaperPath(workspaceId, centerId, from)); } }}>
            <rect x="-8" y="-8" width="296" height="164" rx="27" fill="#6c5bc4" fillOpacity=".08" />
            <rect width="280" height="148" rx="21" fill={`url(#${prefix}-center)`} />
            <circle cx="25" cy="26" r="4" fill="#c4baff" /><text x="37" y="30" fontSize="11" fill="#d6d0fb" letterSpacing="2">中心论文</text>
            <text x="22" y="58" fill="white" fontWeight="600" fontSize="14">{lines(paper.title, 27, 3).map((line, i) => <tspan key={i} x="22" dy={i ? 20 : 0}>{line}</tspan>)}</text>
            <text x="22" y="130" fill="#c4bdf0" fontSize="11">{paper.year ?? "年份未知"} · 点击阅读论文</text>
          </g>
        </g>
      </svg>
      <div className="map-toolbar" aria-label="关系图控制"><button aria-label="放大关系图" disabled={zoom >= 2.5} onClick={() => setZoom(z => Math.min(2.5, z + .25))}><PlusOutlined /></button><span>{Math.round(zoom * 100)}%</span><button aria-label="缩小关系图" disabled={zoom <= .5} onClick={() => setZoom(z => Math.max(.5, z - .25))}><MinusOutlined /></button><i /><button aria-label="重置关系图视角" onClick={reset}><AimOutlined /></button></div>
      <div className="map-interaction-hint">拖动探索 · 点击节点查看关联依据</div>
      {!total ? <div className="map-no-results"><Empty description="本次暂未找到相关文献，可点击“更新关系”重试。" /></div> : null}
    </div>
    <footer className="relation-map-footer"><span>每类展示前 5 篇 · 连线越粗，关联越强 · 同一文献可出现在多个分支</span><span>来源 arXiv / Semantic Scholar</span></footer>
    <Drawer title={selected ? `${selected.channel.label} · 关联详情` : "关联详情"} open={!!selected} onClose={() => setSelected(null)} width={440} rootClassName="relation-detail-drawer">
      {selected ? <div className="relation-detail">
        <span className="relation-detail-tag" style={{ color: selected.channel.color, background: selected.channel.light }}>{selected.channel.label}</span>
        <h2>{selected.item.title}</h2><p className="muted">{selected.item.year ?? "年份未知"} · {selected.item.authors?.slice(0, 3).join(" · ") || "作者未知"}</p>
        <div className="relation-detail-score"><strong style={{ color: selected.channel.color }}>{selected.item.strength}<small>/100</small></strong><span>综合关联强度</span></div>
        <h3>为什么关联？</h3><ul className="relation-reasons">{selected.item.reasons.map(reason => <li key={reason.type}>{reason.text}</li>)}</ul>
        {selected.item.shared_keywords?.length ? <><h3>共同关键词</h3><div className="relation-keywords">{selected.item.shared_keywords.map(term => <span key={term}>{term}</span>)}</div></> : null}
        {selected.item.evidence?.length ? <details className="relation-evidence"><summary>查看引用依据</summary><ul>{selected.item.evidence.map((e, i) => <li key={i}>{{ out: "中心论文引用该文献", in: "该文献引用中心论文", bibliographic_coupling: "共同参考文献", co_citation: "共同引用者" }[e.type] ?? e.type} · <Link to={paperLocation(e.via, from)}>{e.via}</Link></li>)}</ul></details> : null}
        <p className="relation-detail-note">引用扩展为抽样结果；主题与关键词分数基于本次候选计算。推荐文献不会自动加入工作区。</p>
        <Link className="relation-detail-open" to={paperLocation(selected.item.id, from)}>查看论文详情 <span>↗</span></Link>
      </div> : null}
    </Drawer>
  </section>;
}
