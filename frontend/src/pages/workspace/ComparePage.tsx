import { useEffect, useRef, useState } from "react";
import { Button, Empty, Input, Spin, message } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { fetchRelate, importPaper, prepareWorkspace } from "../../api";
import { RelationBoard } from "../../components/RelationBoard";
import { parseSeeds, toggleSelection } from "../../relationLayout";
import { mergeRelationSnapshots, paperSnapshot } from "../../relationGraph";
import { findKey, projects } from "../../projects";
import { paperKey, type RecommendationItem, type RelateResponse } from "../../types";
import { MissingWorkspace, useWorkspaceProject } from "./shared";
import "../../styles/paper-relations.css";

export default function ComparePage() {
  const { id, project } = useWorkspaceProject();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [data, setData] = useState<RelateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [request, setRequest] = useState({ seq: 0, refresh: false });
  const cache = useRef(new Map<string, RelateResponse>());
  const entries = Object.entries(project?.papers ?? {});
  const validKeys = entries.map(([key]) => key);
  const selected = params.has("seeds") ? parseSeeds(params.get("seeds"), validKeys) : validKeys.slice(0, 1);
  const selection = selected.join(",");
  const here = `/w/${id}/compare?${new URLSearchParams({ seeds: selection })}`;

  useEffect(() => { document.title = `${project?.name ?? "工作区"} - 论文关系`; }, [project?.name]);
  useEffect(() => {
    if (!selection) { setData(null); setError(""); setLoading(false); return; }
    const controller = new AbortController();
    const keys = selection.split(",");
    const snapshots = new Map(keys.map(key => [key, cache.current.get(`${id}:${key}`)
      ?? paperSnapshot(project!.papers[key].paper, key)]));
    const publish = () => {
      if (!controller.signal.aborted) setData(mergeRelationSnapshots(keys.map(key => snapshots.get(key)!)));
    };
    publish();
    setError("");
    const missing = keys.filter(key => request.refresh || !cache.current.has(`${id}:${key}`));
    if (!missing.length) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    void Promise.allSettled(missing.map(async key => {
      const result = await fetchRelate([key], request.refresh, controller.signal);
      if (controller.signal.aborted) return;
      if (!result.centers.length) throw new Error("这篇论文的关系还没有取到");
      // An upstream timeout keeps the previous usable result, and remains retryable.
      const incomplete = result.satellites.some(g => g.status === "unavailable" || g.status === "partial");
      if (!incomplete || !cache.current.has(`${id}:${key}`)) {
        cache.current.set(`${id}:${key}`, result);
        snapshots.set(key, result);
      }
      publish();
      if (incomplete) throw new Error("部分关系还没有取全");
    })).then(results => {
      if (controller.signal.aborted) return;
      if (results.some(r => r.status === "rejected")) setError("部分关系还没有取全，已取得的关系仍保留。");
      setLoading(false);
    });
    return () => controller.abort();
  }, [id, selection, request]);

  if (!project) return <MissingWorkspace />;
  function select(next: string[]) {
    setData(previous => {
      if (!previous) return previous;
      const centers = previous.centers.filter(c => next.some(key => key === c.id || key === c.local_id || c.aliases?.includes(key)));
      const ids = new Set(centers.map(c => c.id));
      return { ...previous, centers, satellites: previous.satellites.filter(g => ids.has(g.center_id)) };
    });
    setParams(new URLSearchParams({ seeds: next.join(",") }));
    setRequest(r => ({ seq: r.seq + 1, refresh: false }));
  }
  function toggle(key: string) {
    const next = toggleSelection(selected, key);
    if (next === null) { message.warning("最多同时展开 5 篇主论文"); return; }
    select(next);
  }
  function refresh() {
    setRequest(r => ({ seq: r.seq + 1, refresh: true }));
  }
  async function save(item: RecommendationItem, expand: boolean) {
    if (saving || loading) return;
    if (expand && selected.length >= 5) { setSaveError("最多同时展开 5 篇主论文，请先收起一篇。"); return; }
    setSaving(true); setSaveError("");
    try {
      const existing = findKey(project, item) || entries.find(([key, { paper }]) =>
        [item.id, ...(item.aliases ?? [])].some(alias => alias === key || alias === paper.id
          || alias === `arxiv:${paper.arxiv_id}` || alias === `doi:${paper.doi?.toLowerCase()}` || alias === `s2:${paper.semantic_scholar_id}`))?.[0];
      const saved = existing ? project!.papers[existing].paper : (await importPaper(item.id)).paper;
      // Persist workspace membership before updating local selection or expanding the graph.
      await prepareWorkspace(id, project!.name, [saved.local_id ?? saved.id]);
      projects.select(id); projects.add(saved);
      if (expand) {
        const key = existing ?? paperKey(saved);
        select([...new Set([...selected, key])]);
        message.success("已加入论文库，正在展开它的关联论文");
      } else message.success("已加入当前工作区的论文库");
    } catch (err) { setSaveError(err instanceof Error ? err.message : "保存失败，请重试"); }
    finally { setSaving(false); }
  }
  function remove(centerId: string) {
    const center = data?.centers.find(c => c.id === centerId);
    select(selected.filter(key => key !== centerId && key !== center?.local_id && !center?.aliases?.includes(key)));
  }
  const visible = entries.filter(([, item]) => `${item.paper.title} ${item.paper.authors.join(" ")} ${item.paper.year ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <main className="ws-page relations-page">
    <header className="relations-top"><h1>论文关系</h1>
      <Button icon={<ReloadOutlined />} loading={loading} disabled={!selected.length || loading || saving} onClick={refresh}>刷新关系</Button></header>
    {!entries.length ? <div className="relations-empty"><Empty description="先加入一篇论文，开始探索它的关系网络。" /><Link to="/search">去搜索论文</Link></div> : <>
      <section className="relations-chips" aria-label="选择主论文">
        <Input prefix={<SearchOutlined />} placeholder="搜索论文、作者或年份" aria-label="搜索工作区论文" value={query} onChange={e => setQuery(e.target.value)} allowClear />
        <div className="chip-strip">{visible.map(([key, item]) => <button key={key} type="button" title={item.paper.title} aria-pressed={selected.includes(key)} disabled={saving}
          className={`chip${selected.includes(key) ? " is-selected" : ""}`} onClick={() => toggle(key)}>
          <span className="chip-check">{selected.includes(key) ? "✓" : "+"}</span><span className="chip-title">{item.paper.title}</span></button>)}
          {!visible.length ? <span className="chip-no-results">未找到匹配论文</span> : null}</div>
        <span className="relations-selection-count">{selected.length}/5 已展开</span>
      </section>
      <div className="relations-board-wrap">
        {selected.length && data?.centers.length ? <RelationBoard data={data} workspaceId={id} from={here} busy={loading} saving={saving} error={saveError}
          libraryIds={entries.flatMap(([key, { paper }]) => [key, paper.id, ...(paper.arxiv_id ? [`arxiv:${paper.arxiv_id}`] : []), ...(paper.doi ? [`doi:${paper.doi.toLowerCase()}`] : []), ...(paper.semantic_scholar_id ? [`s2:${paper.semantic_scholar_id}`] : [])])}
          onSave={save} onRemove={remove} /> :
          <div className="relations-loading" role="status">{loading ? <Spin /> : null}<h2>{loading ? "正在构建论文关系空间" : selected.length ? "关系还没有取到" : "选择一篇或多篇主论文"}</h2>
            {loading ? <p>正在查找引用、主题与关键词三类关联</p> : null}{error ? <Button onClick={refresh}>重试</Button> : null}</div>}
        {error ? <div className="relations-retry" role="status">{error}<button disabled={loading} onClick={refresh}>重试</button></div> : null}
      </div>
    </>}
  </main>;
}
