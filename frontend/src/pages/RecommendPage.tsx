import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Skeleton, Tabs } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { fetchLibrary, fetchNetwork, fetchRecommendations } from "../api";
import { compareBlockReason, useAnalysisStates } from "../components/AnalysisBadge";
import { CollectButton } from "../components/CollectButton";
import { PaperContext } from "../components/PaperContext";
import { PaperRow } from "../components/PaperRow";
import { RelationGraph } from "../components/RelationGraph";
import { findKey, useProjects } from "../projects";
import type { Paper, PaperNetwork, RecommendationItem, Recommendations } from "../types";
import { paperKey } from "../types";
import { paperLocation, recommendLocation, safeReturn, workspacePaperPath } from "../workspace";

function scoreText(value: number | null) {
  return value == null ? "未知／暂不可用" : value.toFixed(2);
}

function citationText(value: number | null) {
  if (value == null) return "未知／暂不可用";
  return value >= 1 ? "直接引用" : value > 0 ? "间接关联" : "未发现";
}

function asPaper(item: RecommendationItem): Paper {
  return {
    id: item.id,
    title: item.title,
    authors: item.authors ?? [],
    abstract: item.abstract ?? null,
    year: item.year,
    venue: item.venue ?? null,
    sources: item.sources ?? [],
    url: item.url ?? null,
    pdf_url: null,
    citation_count: item.citation_count ?? null,
    arxiv_id: item.id.startsWith("arxiv:") ? item.id.slice(6) : null,
    doi: item.id.startsWith("doi:") ? item.id.slice(4) : null,
    semantic_scholar_id: item.id.startsWith("s2:") ? item.id.slice(3) : null,
    ccf_rank: null,
    cas_partition: null,
  };
}

function RelationLabel({ item }: { item: RecommendationItem }) {
  return <span className="relation-labels">
    {item.relations.includes("cites") ? <span className="relation relation-cites">引用关系</span> : null}
    {item.relations.includes("similar") ? <span className="relation relation-similar">主题相似 · 算法推算</span> : null}
    {item.categories?.includes("citation") && !item.relations.includes("cites") ? <span className="relation relation-cites">间接引用关联</span> : null}
    {item.relations.includes("keyword") ? <span className="relation relation-keyword">关键词关联</span> : null}
    {!item.relations.length ? <span className="relation relation-weak">弱关联</span> : null}
  </span>;
}

function RecommendCard({ item, from }: { item: RecommendationItem; from: string }) {
  const { current } = useProjects();
  const paper = asPaper(item);
  const key = findKey(current, paper);
  const states = useAnalysisStates(key ? [key] : []);
  const reason = key ? compareBlockReason(states[key]) : "未分析";
  const ready = Boolean(key && current && reason === null);
  return <li className="recommend-item">
    <div className="recommend-top"><RelationLabel item={item} /><span className="muted">{item.year ?? "年份未知"}</span></div>
    <h3 className="paper-title"><Link to={paperLocation(item.id, from)}>{item.title}</Link></h3>
    <div className="strength" aria-label={`关联强度 ${item.strength}`}>
      <span className="strength-bar"><i style={{ width: `${Math.max(0, Math.min(100, item.strength))}%` }} /></span>
      <span>关联强度 {item.strength}</span>
      <span className="muted">语义 {scoreText(item.semantic_score)} · 关键词 {item.keyword_score.toFixed(2)} · 引用 {citationText(item.citation_score)}</span>
      <span className="muted">被引 {item.citation_count == null ? "未知／暂不可用" : item.citation_count}</span>
    </div>
    {item.reasons.length ? <ul className="reasons">{item.reasons.map(reasonItem => <li key={reasonItem.type}>{reasonItem.text}</li>)}</ul> : null}
    {item.evidence?.length ? <details className="explain"><summary>查看引用依据</summary><ul>{item.evidence.map(e => <li key={`${e.type}:${e.via}`}>
      {{ out: "当前论文的参考文献", in: "当前论文的被引文献", bibliographic_coupling: "共同参考文献", co_citation: "共同引用者" }[e.type] ?? e.type}：
      <Link to={paperLocation(e.via, from)}>{e.via}</Link> · Semantic Scholar
    </li>)}</ul></details> : null}
    <div className="recommend-actions">
      <CollectButton paper={paper} />
      {ready ? <Link to={`/w/${current!.id}/compare/table?pick=${encodeURIComponent(key)}`}>加入研究内容对比</Link> : null}
      {key && current && !ready ? <Link to={workspacePaperPath(current.id, key, from)}>前往分析</Link> : null}
      {ready ? null : <span className="muted">{!key ? "收藏后完成单篇分析，才能加入研究内容对比" : reason === "状态读取中" ? "正在确认分析状态" : "完成单篇分析后，才能加入研究内容对比"}</span>}
    </div>
  </li>;
}

function StartPicker() {
  const location = useLocation();
  const [choices, setChoices] = useState<Paper[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    fetchLibrary(1, controller.signal).then(data => setChoices(data.papers))
      .catch(err => { if (!controller.signal.aborted) setError(err.message || "文献库加载失败"); });
    return () => controller.abort();
  }, [retry]);
  const here = location.pathname + location.search;
  return <main className="page page-medium">
    <header className="page-head"><div><h1 className="page-title">论文相似度搜索</h1><p className="page-sub">选择中心论文，分别从引用、主题和关键词三个角度发现相关文献。</p></div></header>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
    {!choices && !error ? <Skeleton active /> : null}
    {choices && !choices.length ? <Empty description={<>文献库还没有论文，<Link to="/search">先检索并加入一篇</Link>。</>} /> : null}
    <div className="paper-list">{choices?.map(paper => <PaperRow key={paperKey(paper)} paper={paper} from={here} abstract={false}
      side={<Link to={recommendLocation(paperKey(paper), "/library")}>以此为起点</Link>} />)}</div>
  </main>;
}

export default function RecommendPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const id = params.get("id")?.trim() ?? "";
  const from = safeReturn(params.get("from"));
  const current = `/recommend?${params}`;
  const [request, setRequest] = useState({ seq: 0, refresh: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [networkError, setNetworkError] = useState("");
  const [recommendations, setRecommendations] = useState<Recommendations | null>(null);
  const [network, setNetwork] = useState<PaperNetwork | null>(null);
  const [category, setCategory] = useState("all");

  const openPaper = useCallback((paperId: string) => { navigate(paperLocation(paperId, current)); }, [current, navigate]);

  useEffect(() => {
    document.title = recommendations ? `${recommendations.paper_title} - 论文相似度搜索 - 文献研读` : "论文相似度搜索 - 文献研读";
  }, [recommendations]);

  useEffect(() => {
    if (!id) { setLoading(false); setError(""); setRecommendations(null); setNetwork(null); return; }
    const controller = new AbortController();
    const refresh = request.refresh;
    setLoading(true); setError(""); setNetworkError(""); setRecommendations(null); setNetwork(null);
    const fail = (setMessage: (value: string) => void, fallback: string) => (err: Error) => {
      if (!controller.signal.aborted) setMessage(err.message || fallback);
    };
    const load = async () => {
      if (refresh) {
        const result = await fetchRecommendations(id, true, controller.signal);
        if (controller.signal.aborted) return;
        setRecommendations(result);
        try {
          const graph = await fetchNetwork(id, false, controller.signal);
          if (!controller.signal.aborted) setNetwork(graph);
        } catch (err) {
          fail(setNetworkError, "关系网络加载失败")(err as Error);
        }
        return;
      }
      await Promise.all([
        fetchRecommendations(id, false, controller.signal).then(result => {
          if (!controller.signal.aborted) setRecommendations(result);
        }).catch(fail(setError, "相关推荐加载失败")),
        fetchNetwork(id, false, controller.signal).then(result => {
          if (!controller.signal.aborted) setNetwork(result);
        }).catch(fail(setNetworkError, "关系网络加载失败")),
      ]);
    };
    load().catch(fail(setError, "相关推荐加载失败")).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, request]);

  if (!id) return <StartPicker />;
  const warnings = Array.from(new Set([...(recommendations?.warnings ?? []), ...(network?.warnings ?? [])]));
  const selectedGroup = recommendations?.groups?.find(group => group.key === category);
  const items = category === "all" ? recommendations?.items ?? [] : selectedGroup?.items ?? [];
  const categoryIds = new Set(items.map(item => item.id));
  const graphEdges = network?.edges.filter(edge => category === "all" || (edge.category === category && (categoryIds.has(edge.source) || categoryIds.has(edge.target)))) ?? [];
  const connected = new Set(graphEdges.flatMap(edge => [edge.source, edge.target]));
  const filteredNetwork = network ? { ...network, edges: graphEdges, nodes: network.nodes.filter(node => node.center || connected.has(node.id)) } : null;
  const retry = () => setRequest(r => ({ seq: r.seq + 1, refresh: false }));
  return <>
    <PaperContext id={id} from={from} />
    <main className="page page-wide recommend-page">
      <header className="page-head">
        <div>
          <h1 className="page-title">论文相似度搜索</h1>
          <p className="page-sub">{recommendations ? <>起点：<Link to={paperLocation(id, from)}>{recommendations.paper_title}</Link></> : "正在读取起点论文…"}</p>
        </div>
        <div className="page-actions"><Button icon={<ReloadOutlined />} disabled={loading} onClick={() => setRequest(r => ({ seq: r.seq + 1, refresh: true }))}>重新计算</Button></div>
      </header>
      {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={retry}>重试</Button>} /> : null}
      {warnings.map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
      <p className="muted">从 arXiv 与 Semantic Scholar 获取候选，每类独立保留最多 24 篇，再分别排序。只保存推荐结果和依据，本次候选向量用后释放。</p>
      <Tabs activeKey={category} onChange={setCategory} items={[
        { key: "all", label: "综合推荐" },
        ...([ ["citation", "引用关联"], ["semantic", "主题相似"], ["keyword", "关键词关联"] ] as const).map(([key, label]) => {
          const group = recommendations?.groups?.find(g => g.key === key);
          return { key, label: `${label}${group ? ` (${group.items.length})` : ""}` };
        }),
      ]} />
      {selectedGroup ? <p className="muted">该通道召回 {selectedGroup.recall_count} 篇 · 合并候选中符合本类条件 {selectedGroup.matched_count} 篇 · 展示 {selectedGroup.items.length} 篇{selectedGroup.status !== "completed" ? " · 本类数据暂不完整" : ""}。同一论文可属于多类；引用扩展为抽样，主题分数仅描述本次候选。</p> : null}
      <div className="recommend-layout">
        <section className="recommend-list" aria-label="推荐列表" aria-busy={loading}>
          {recommendations ? <details className="explain">
            <summary>推荐分数如何计算</summary>
            <p>{recommendations.formula}</p>
            <p>{recommendations.note}</p>
            {recommendations.search_plan ? <p>主题检索：{recommendations.search_plan.semantic.join(" / ")}；关键词检索：{recommendations.search_plan.keyword.join(" / ")}</p> : null}
            {recommendations.source_status?.map((source, index) => <p className="muted" key={index}>{source.source} · {source.query ?? "引用查询"}：{source.status === "completed" ? `${source.count} 篇` : source.message || "暂不可用"}</p>)}
            <p className="muted">算法 {recommendations.algorithm_version} · 计算于 {new Date(recommendations.computed_at).toLocaleString("zh-CN")}。结果会保存以便再次查看，推荐论文不会自动进入论文库。</p>
          </details> : null}
          {loading && !recommendations ? <div className="row-skeletons">{[0, 1, 2].map(i => <Skeleton key={i} active paragraph={{ rows: 2 }} />)}</div> : null}
          {recommendations && !items.length ? <Empty description={selectedGroup?.status === "unavailable" ? "本类数据暂不可用，可查看其他关系类型" : "本次候选中没有符合该类别的论文"} /> : null}
          <ol className="recommend-items">{items.map(item => <RecommendCard key={item.id} item={item} from={current} />)}</ol>
        </section>
        <section className="panel network-panel" aria-labelledby="network-title">
          <h2 id="network-title">关系网络</h2>
          <p className="legend">
            <span><i className="swatch" />实线箭头：引用（Semantic Scholar 元数据）</span>
            <span><i className="swatch similar" />虚线：主题相似（算法推算，不是引用）</span>
            <span><i className="swatch keyword" />绿色点线：关键词关联</span>
            <span><i className="swatch indirect" />紫色虚线：共同参考文献／共被引（抽样）</span>
          </p>
          {networkError ? <Alert type="error" showIcon message={networkError} action={<Button size="small" onClick={retry}>重试</Button>} /> : null}
          {!network && loading ? <Skeleton.Node active className="network-skeleton"><span /></Skeleton.Node> : null}
          {filteredNetwork?.nodes.length ? <RelationGraph data={filteredNetwork} onOpen={openPaper} /> : null}
          {filteredNetwork && filteredNetwork.nodes.length <= 1 ? <p className="muted">当前类别暂无可连接的候选。</p> : null}
          <p className="muted small-print">点击节点打开对应论文，可拖动和缩放画布。</p>
        </section>
      </div>
    </main>
  </>;
}
