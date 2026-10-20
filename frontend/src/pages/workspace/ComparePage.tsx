import { useEffect, useRef, useState } from "react";
import { Button, Empty, Input, Spin, message } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { fetchRelate } from "../../api";
import { RelationBoard } from "../../components/RelationBoard";
import { parseSeeds, resolveFocus, toggleSelection } from "../../relationLayout";
import type { RelateResponse } from "../../types";
import { MissingWorkspace, useWorkspaceProject } from "./shared";
import "../../styles/paper-relations.css";

export default function ComparePage() {
  const { id, project } = useWorkspaceProject();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [data, setData] = useState<RelateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [request, setRequest] = useState({ seq: 0, refresh: false });
  const cache = useRef(new Map<string, RelateResponse>());
  const entries = Object.entries(project?.papers ?? {});
  const validKeys = entries.map(([key]) => key);
  const rawSeeds = params.get("seeds");
  const selected = rawSeeds === null ? (entries[0]?.[0] ? [entries[0][0]] : []) : parseSeeds(rawSeeds, validKeys);
  const focus = resolveFocus(selected, params.get("focus"));
  const visible = entries.filter(([, item]) => `${item.paper.title} ${item.paper.authors.join(" ")} ${item.paper.year ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const hereParams = new URLSearchParams({ seeds: selected.join(",") });
  if (focus) hereParams.set("focus", focus);
  const here = `/w/${id}/compare?${hereParams}`;

  useEffect(() => { document.title = `${project?.name ?? "工作区"} - 论文关系`; }, [project?.name]);
  useEffect(() => {
    if (!focus) { setData(null); setError(""); setLoading(false); return; }
    const controller = new AbortController();
    const cacheKey = `${id}:${focus}`;
    const cached = cache.current.get(cacheKey);
    setData(cached ?? null); setError("");
    if (cached && !request.refresh) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    fetchRelate([focus], request.refresh, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      cache.current.set(cacheKey, result); setData(result);
    }).catch((err: Error) => { if (!controller.signal.aborted) setError(err.message || "关系暂时没有取到"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, focus, request]);

  if (!project) return <MissingWorkspace />;
  function updateParams(next: string[], nextFocus: string | null) {
    const search = new URLSearchParams({ seeds: next.join(",") });
    if (nextFocus) search.set("focus", nextFocus);
    setParams(search);
    setRequest(r => ({ seq: r.seq + 1, refresh: false }));
  }
  function toggle(key: string) {
    if (selected.includes(key)) {
      if (key === focus) {
        const next = toggleSelection(selected, key) ?? [];
        updateParams(next, next.includes(focus) ? focus : null);
      } else {
        updateParams(selected, key);
      }
      return;
    }
    const next = toggleSelection(selected, key);
    if (next === null) { message.warning("最多支持 5 篇核心论文"); return; }
    updateParams(next, focus);
  }
  function refresh() {
    cache.current.delete(`${id}:${focus}`);
    setRequest(r => ({ seq: r.seq + 1, refresh: true }));
  }

  const center = data?.centers[0] ?? null;
  const titleOf = new Map(entries.map(([key, item]) => [key, item.paper.title]));
  const badgeOf = new Map<string, string>();
  if (data) {
    for (const key of selected) {
      if (key === focus) continue;
      const title = titleOf.get(key);
      if (!title) continue;
      const hit = data.satellites.flatMap(group => group.items).find(item => item.title === title);
      if (!hit) continue;
      if (hit.categories?.includes("citation")) badgeOf.set(key, "与当前论文有引用");
      else if (hit.categories?.includes("semantic")) badgeOf.set(key, "主题相近");
    }
  }

  return <main className="ws-page relations-page">
    <header className="relations-top">
      <h1>论文关系</h1>
      <p>查看一篇核心论文的引用、主题和关键词关联</p>
      <Button icon={<ReloadOutlined />} loading={loading} disabled={!focus || loading} onClick={refresh}>刷新</Button>
    </header>
    {!entries.length ? <div className="relations-empty"><Empty description="工作区还没有论文，先添加一篇，开始探索它的关系网络。" /><Link to="/search">去搜索论文 →</Link></div> : <>
      <section className="relations-chips" aria-label="选择核心论文">
        <Input prefix={<SearchOutlined />} placeholder="搜索标题、作者或年份" aria-label="搜索工作区论文" value={query} onChange={e => setQuery(e.target.value)} allowClear />
        <div className="chip-strip">
          {visible.map(([key, item]) => {
            const isSelected = selected.includes(key);
            const isFocus = key === focus;
            return <button key={key} type="button" title={item.paper.title} aria-pressed={isSelected}
              className={`chip${isSelected ? " is-selected" : ""}${isFocus ? " is-focus" : ""}`}
              onClick={() => toggle(key)}>
              <span className="chip-title">{item.paper.title}</span>
              {isFocus ? <span className="chip-flag">当前</span> : null}
              {badgeOf.has(key) ? <span className="chip-badge">{badgeOf.get(key)}</span> : null}
            </button>;
          })}
          {!visible.length ? <span className="chip-no-results">未找到匹配的论文</span> : null}
        </div>
      </section>
      {selected.length > 1 ? <div className="relations-focusbar">
        <span className="relations-focusbar-current">当前：{center?.title ?? titleOf.get(focus ?? "") ?? ""}</span>
        <span className="relations-focusbar-switch">切换：{selected.filter(key => key !== focus).map(key => (
          <button key={key} type="button" onClick={() => updateParams(selected, key)}>{titleOf.get(key) ?? key}</button>
        ))}</span>
      </div> : null}
      <div className="relations-board-wrap">
        {center && focus ? <RelationBoard key={`${id}:${focus}`} center={center}
          groups={data!.satellites.filter(group => group.center_id === center.id)}
          workspaceId={id} from={here} onRetry={refresh} /> :
          <div className="relations-loading" role="status">
            {error && !loading ? <><h2>关系暂时没有取到</h2><p>{error}</p><Button onClick={refresh}>重试</Button></> :
              <><Spin /><h2>{loading ? "正在查找引用关联、主题相似和关键词关联" : "等待选择"}</h2>
                <p>{loading ? "正在计算当前核心论文的三类关联。" : "选择 1 到 5 篇论文，查看其中一篇的关系。"}</p></>}
          </div>}
      </div>
    </>}
  </main>;
}
