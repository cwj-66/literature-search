import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { CloseOutlined, FullscreenExitOutlined, FullscreenOutlined, PlusOutlined, RedoOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { assignSections, arrowOf, metricText, type RelationSection } from "../relationLayout";
import type { RecommendationItem, RelateCenter, RelateSatelliteGroup } from "../types";
import { paperLocation, workspacePaperPath } from "../workspace";

const SECTION_META: Record<string, { label: string; color: string; dash?: string; styleName: string }> = {
  citation: { label: "引用关联", color: "#2563EB", styleName: "实线" },
  semantic: { label: "主题相似", color: "#0F766E", dash: "6 4", styleName: "虚线" },
  keyword: { label: "关键词关联", color: "#B45309", dash: "2 4", styleName: "点线" },
};
const RANK_WIDTHS = [2.6, 2.1, 1.7, 1.4, 1.2];
const ZOOM_STEP = 0.25;
const ZOOM_MAX = 1.5;

type BoardLine = {
  id: string;
  d: string;
  color: string;
  dash?: string;
  width: number;
  startArrow: boolean;
  endArrow: boolean;
};

function ArrowDefs() {
  return <defs>
    {Object.entries(SECTION_META).map(([key, meta]) => (
      <marker key={key} id={`rb-arrow-${key}`} viewBox="0 0 10 10" refX="8" refY="5"
        markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 1 L 9 5 L 0 9" fill="none" stroke={meta.color} strokeWidth="1.6" />
      </marker>
    ))}
  </defs>;
}

function SectionHead({ section, count }: { section: RelationSection; count: number }) {
  const meta = SECTION_META[section.key];
  return <header className="rb-column-head">
    <div className="rb-column-title">
      <svg width="30" height="8" aria-hidden="true">
        <line x1="0" y1="4" x2="30" y2="4" stroke={meta.color} strokeWidth="1.8" strokeDasharray={meta.dash} />
      </svg>
      <strong>{meta.label}</strong>
      <span className="rb-count">{count}</span>
      <span className="rb-style-name">{meta.styleName}</span>
    </div>
    {section.hidden > 0 ? <p className="rb-hidden-note">另有 {section.hidden} 篇显示在其他分区</p> : null}
  </header>;
}

function SectionFoot({ section, onRetry }: { section: RelationSection; onRetry: () => void }) {
  if (section.status === "unavailable" && !section.items.length) {
    return <p className="rb-section-note">暂时没有取到，<button type="button" className="rb-retry" onClick={onRetry}>重试</button></p>;
  }
  return <>
    {section.status === "partial" ? <p className="rb-section-note">这一区的结果可能不完整</p> : null}
    {section.status === "completed" && section.matchedCount === 0 ? <p className="rb-section-note">这一类里还没有找到关联论文</p> : null}
  </>;
}

function PaperCard({ entry, sectionKey, dim, onOpen, register }: {
  entry: { item: RecommendationItem; alsoIn: string[] };
  sectionKey: string;
  dim: boolean;
  onOpen: () => void;
  register: (el: HTMLElement | null) => void;
}) {
  const { item, alsoIn } = entry;
  const meta = SECTION_META[sectionKey];
  return <article ref={register} className={`rb-card${dim ? " is-dim" : ""}`} style={{ borderLeftColor: meta.color }}
    onClick={(event) => { event.stopPropagation(); onOpen(); }} title={item.title}>
    <h4>{item.title}</h4>
    <p className="rb-metric">{item.year ?? "年份未知"} · {metricText(item, sectionKey)}</p>
    {alsoIn.length ? <p className="rb-also">也属于{alsoIn.map((key) => SECTION_META[key]?.label ?? key).join("、")}</p> : null}
  </article>;
}

export function RelationBoard({ center, groups, workspaceId, from, onRetry }: {
  center: RelateCenter;
  groups: RelateSatelliteGroup[];
  workspaceId: string;
  from: string;
  onRetry: () => void;
}) {
  const sections = useMemo(() => assignSections(groups), [groups]);
  const [panel, setPanel] = useState<{ item: RecommendationItem; categories: string[] } | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 759px)").matches);
  const [tab, setTab] = useState<string>("citation");
  const [lines, setLines] = useState<BoardLine[]>([]);
  const boardRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const coreRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef(new Map<string, HTMLElement>());
  const navigate = useNavigate();

  useEffect(() => {
    const media = window.matchMedia("(max-width: 759px)");
    const onChange = () => setNarrow(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const measure = useCallback(() => {
    const content = contentRef.current;
    const core = coreRef.current;
    if (!content || !core || narrow) { setLines([]); return; }
    const coreRect = core.getBoundingClientRect();
    const cRect = content.getBoundingClientRect();
    const local = (x: number, y: number): [number, number] => [(x - cRect.left) / zoom, (y - cRect.top) / zoom];
    const [x1, y1] = local((coreRect.left + coreRect.right) / 2, coreRect.bottom);
    const next: BoardLine[] = [];
    for (const section of sections) {
      const meta = SECTION_META[section.key];
      section.items.forEach(({ item }, rank) => {
        const el = cardRefs.current.get(item.id);
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const [x2, y2] = local((rect.left + rect.right) / 2, rect.top);
        const midY = y1 + Math.max(20, (y2 - y1) * 0.45);
        const arrow = arrowOf(item);
        next.push({
          id: item.id,
          d: `M ${x1} ${y1} L ${x1} ${midY} L ${x2} ${midY} L ${x2} ${y2}`,
          color: meta.color,
          dash: meta.dash,
          width: RANK_WIDTHS[rank] ?? RANK_WIDTHS[RANK_WIDTHS.length - 1],
          startArrow: arrow === "in" || arrow === "both",
          endArrow: arrow === "out" || arrow === "both",
        });
      });
    }
    setLines(next);
  }, [sections, narrow, zoom]);

  useLayoutEffect(() => { measure(); }, [measure]);
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(content);
    return () => observer.disconnect();
  }, [measure]);

  const openPanel = (item: RecommendationItem, categories: string[]) => {
    setPanel({ item, categories });
    setActiveId(item.id);
  };
  const closePanel = () => { setPanel(null); setActiveId(null); };
  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void boardRef.current?.requestFullscreen();
  };

  const lineFor = (line: BoardLine) => (
    <path key={line.id} d={line.d} fill="none" stroke={line.color} strokeWidth={line.width}
      strokeDasharray={line.dash} opacity={activeId && activeId !== line.id ? 0.2 : 0.85}
      markerStart={line.startArrow ? `url(#rb-arrow-${markerKey(line.color)})` : undefined}
      markerEnd={line.endArrow ? `url(#rb-arrow-${markerKey(line.color)})` : undefined}
      pointerEvents="stroke" style={{ cursor: "pointer" }}
      onClick={(event) => { event.stopPropagation(); const entry = findEntry(line.id); if (entry) openPanel(entry.item, entry.categories); }} />
  );
  const markerKey = (color: string) => Object.keys(SECTION_META).find((key) => SECTION_META[key].color === color) ?? "citation";
  const findEntry = (id: string) => {
    for (const section of sections) {
      const found = section.items.find((entry) => entry.item.id === id);
      if (found) return { item: found.item, categories: [section.key, ...found.alsoIn] };
    }
    return null;
  };

  const reasons = panel ? [...new Map(panel.item.reasons.map((reason) => [reason.text, reason])).values()] : [];
  const direction = panel ? arrowOf(panel.item) : null;

  const columnOf = (section: RelationSection) => (
    <section className="rb-column" key={section.key} aria-label={SECTION_META[section.key].label}>
      <SectionHead section={section} count={section.items.length} />
      <div className="rb-column-list" onScroll={measure}>
        {section.items.map((entry) => (
          <PaperCard key={entry.item.id} entry={entry} sectionKey={section.key}
            dim={Boolean(activeId && activeId !== entry.item.id)}
            onOpen={() => openPanel(entry.item, [section.key, ...entry.alsoIn])}
            register={(el) => { if (el) cardRefs.current.set(entry.item.id, el); else cardRefs.current.delete(entry.item.id); }} />
        ))}
        <SectionFoot section={section} onRetry={onRetry} />
      </div>
    </section>
  );

  return <div className={`rb-board${fullscreen ? " is-fullscreen" : ""}`} ref={boardRef} aria-label="论文关系板"
    onClick={() => { if (panel) closePanel(); }}>
    <div className="rb-toolbar" aria-label="视图控制" onClick={(event) => event.stopPropagation()}>
      <button type="button" aria-label="放大" disabled={zoom >= ZOOM_MAX}
        onClick={() => setZoom((value) => Math.min(ZOOM_MAX, value + ZOOM_STEP))}><PlusOutlined /></button>
      <button type="button" aria-label="恢复 100%" disabled={zoom === 1} onClick={() => setZoom(1)}><RedoOutlined /></button>
      <span className="rb-zoom">{Math.round(zoom * 100)}%</span>
      <button type="button" aria-label={fullscreen ? "退出全屏" : "全屏"} onClick={toggleFullscreen}>
        {fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
      </button>
    </div>
    <div className="rb-scroll">
      <div className="rb-zoom-box" style={narrow ? undefined : { width: `${zoom * 100}%`, height: `${zoom * 100}%` }}>
        <div className="rb-content" ref={contentRef}
          style={narrow ? undefined : { transform: `scale(${zoom})`, width: `${100 / zoom}%`, height: `${100 / zoom}%` }}>
          {!narrow ? <svg className="rb-lines" aria-hidden="true"><ArrowDefs />{lines.map(lineFor)}</svg> : null}
          <div className="rb-core" ref={coreRef} role="button" tabIndex={0} title={center.title}
            onClick={(event) => { event.stopPropagation(); navigate(workspacePaperPath(workspaceId, center.local_id ?? center.id, from)); }}
            onKeyDown={(event) => { if (event.key === "Enter") navigate(workspacePaperPath(workspaceId, center.local_id ?? center.id, from)); }}>
            <span className="rb-core-tag">核心论文</span>
            <h3>{center.title}</h3>
            <p>{center.year ?? "年份未知"} · {center.authors.slice(0, 2).join(" · ") || "作者未知"}<span className="rb-core-open">点击进入论文页</span></p>
          </div>
          {narrow ? <>
            <nav className="rb-tabs" aria-label="关系分区">
              {sections.map((section) => (
                <button key={section.key} type="button" aria-pressed={tab === section.key}
                  style={tab === section.key ? { borderColor: SECTION_META[section.key].color, color: SECTION_META[section.key].color } : undefined}
                  onClick={(event) => { event.stopPropagation(); setTab(section.key); }}>
                  {SECTION_META[section.key].label} {section.items.length}
                </button>
              ))}
            </nav>
            <div className="rb-list">
              {sections.filter((section) => section.key === tab).map((section) => (
                <div key={section.key} className="rb-list-group">
                  {section.items.map((entry) => (
                    <PaperCard key={entry.item.id} entry={entry} sectionKey={section.key}
                      dim={Boolean(activeId && activeId !== entry.item.id)}
                      onOpen={() => openPanel(entry.item, [section.key, ...entry.alsoIn])}
                      register={() => undefined} />
                  ))}
                  <SectionFoot section={section} onRetry={onRetry} />
                </div>
              ))}
            </div>
          </> : <div className="rb-columns">{sections.map(columnOf)}</div>}
        </div>
      </div>
    </div>
    {panel ? <aside className="rb-panel" aria-label="相关论文详情" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="rb-panel-close" aria-label="关闭详情" onClick={closePanel}><CloseOutlined /></button>
      <div className="rb-panel-tags">{panel.categories.map((category) => <span key={category}>{SECTION_META[category]?.label ?? category}</span>)}</div>
      <h2>{panel.item.title}</h2>
      <p className="rb-panel-meta">{panel.item.year ?? "年份未知"} · {panel.item.authors?.slice(0, 3).join(" · ") || "作者未知"}</p>
      <div className="rb-panel-scores">
        {panel.categories.map((category) => (
          <div key={category}><span>{SECTION_META[category]?.label ?? category}</span><strong>{metricText(panel.item, category)}</strong></div>
        ))}
      </div>
      {direction === "both" ? <p className="rb-panel-direction">双方有互引记录</p> : null}
      {panel.item.abstract ? <><h3>摘要</h3><p className="rb-panel-abstract">{panel.item.abstract}</p></> : null}
      {reasons.length ? <><h3>关联依据</h3><ul className="rb-panel-reasons">{reasons.map((reason) => <li key={reason.type + reason.text}>{reason.text}</li>)}</ul></> : null}
      {panel.item.shared_keywords?.length ? (
        <div className="rb-panel-keywords">{panel.item.shared_keywords.map((term) => <span key={term}>{term}</span>)}</div>
      ) : null}
      <div className="rb-panel-links">
        <Link to={paperLocation(panel.item.id, from)}>查看论文详情 ↗</Link>
        {panel.item.url ? <a href={panel.item.url} target="_blank" rel="noreferrer">来源链接 ↗</a> : null}
      </div>
    </aside> : null}
  </div>;
}
