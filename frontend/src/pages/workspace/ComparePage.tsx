import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, Spin } from "antd";
import { LeftOutlined, ReloadOutlined, RightOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { fetchRecommendations } from "../../api";
import { PaperRelationMap } from "../../components/PaperRelationMap";
import type { Recommendations } from "../../types";
import { MissingWorkspace, useWorkspaceProject } from "./shared";
import "../../styles/paper-relations.css";

export default function ComparePage() {
  const { id, project } = useWorkspaceProject();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [data, setData] = useState<Recommendations | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [request, setRequest] = useState({ seq: 0, refresh: false });
  const strip = useRef<HTMLDivElement>(null);
  const cache = useRef(new Map<string, Recommendations>());
  const entries = Object.entries(project?.papers ?? {});
  const requested = params.get("seed") || params.get("pick") || "";
  const seed = entries.some(([key]) => key === requested) ? requested : entries[0]?.[0] ?? "";
  const paper = project?.papers[seed]?.paper;
  const visible = entries.filter(([, item]) => `${item.paper.title} ${item.paper.authors.join(" ")} ${item.paper.year ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const here = `/w/${id}/compare?${new URLSearchParams({ seed })}`;

  useEffect(() => { document.title = `${project?.name ?? "工作区"} - 论文关系`; }, [project?.name]);
  useEffect(() => {
    if (!seed) { setData(null); setError(""); setLoading(false); return; }
    const controller = new AbortController();
    const cacheKey = `${id}:${seed}`;
    const cached = cache.current.get(cacheKey);
    setData(cached ?? null); setError("");
    if (cached && !request.refresh) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    fetchRecommendations(seed, request.refresh, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      cache.current.set(cacheKey, result); setData(result);
    }).catch((err: Error) => { if (!controller.signal.aborted) setError(err.message || "关系网络加载失败，请重试"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, seed, request]);

  if (!project) return <MissingWorkspace />;
  function choose(key: string) {
    if (key === seed) return;
    setParams({ seed: key });
    setRequest(r => ({ seq: r.seq + 1, refresh: false }));
  }
  return <main className="ws-page relations-page">
    <header className="relations-heading">
      <div><div className="relations-eyebrow">PAPER CONNECTIONS <span>发现研究的下一条线索</span></div>
        <h1>论文关系<span>从一篇出发，连接更多可能</span></h1>
        <p>以论文为中心，从引用关联、主题相似和关键词关联三个角度探索相关文献。</p>
      </div>
      <Button icon={<ReloadOutlined />} loading={loading} disabled={!seed} onClick={() => setRequest(r => ({ seq: r.seq + 1, refresh: true }))}>更新关系</Button>
    </header>
    {!entries.length ? <div className="relations-empty"><Empty description="工作区还没有论文，先添加一篇，开始探索它的关系网络。" /><Link to="/search">去搜索论文 →</Link></div> : <>
      <section className="seed-picker" aria-label="选择中心论文">
        <div className="seed-picker-head"><div><strong>中心论文</strong><span>{entries.length} 篇 · 点击切换探索起点</span></div>
          <Input prefix={<SearchOutlined />} placeholder="搜索标题、作者或年份" aria-label="搜索工作区论文" value={query} onChange={e => setQuery(e.target.value)} allowClear />
        </div>
        <div className="seed-strip-wrap">
          <button className="seed-scroll" aria-label="向左浏览论文" onClick={() => strip.current?.scrollBy({ left: -420, behavior: "smooth" })}><LeftOutlined /></button>
          <div className="seed-strip" ref={strip}>
            {visible.map(([key, item], index) => <button key={key} className={`seed-card${key === seed ? " is-selected" : ""}`} aria-pressed={key === seed} onClick={() => choose(key)} title={item.paper.title}>
              <span className="seed-card-meta"><span>{String(index + 1).padStart(2, "0")}</span><span>{item.paper.year ?? "年份未知"}{key === seed ? " · 探索中" : ""}</span></span>
              <strong>{item.paper.title}</strong><span className="seed-card-author">{item.paper.authors.slice(0, 2).join(" · ") || "作者未知"}</span>
            </button>)}
            {!visible.length ? <div className="seed-no-results">未找到匹配的论文，试试其他标题或作者。</div> : null}
          </div>
          <button className="seed-scroll" aria-label="向右浏览论文" onClick={() => strip.current?.scrollBy({ left: 420, behavior: "smooth" })}><RightOutlined /></button>
        </div>
      </section>
      {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRequest(r => ({ seq: r.seq + 1, refresh: false }))}>重试</Button>} /> : null}
      {data?.warnings.map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
      {paper && data ? <PaperRelationMap key={`${id}:${seed}`} data={data} paper={paper} from={here} workspaceId={id} centerId={seed} /> : <div className="relations-loading" role="status"><div className="relations-loading-orbit"><span /><span /><span /><i /></div><Spin /><h2>{loading ? "正在连接相关研究" : "等待探索"}</h2><p>{loading ? "正在查找引用、主题和关键词关联，首次探索可能需要片刻。" : "选择一篇论文，展开关系网络。"}</p></div>}
    </>}
  </main>;
}
