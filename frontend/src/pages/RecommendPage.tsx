import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Skeleton } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { fetchLibrary, fetchNetwork, fetchRecommendations } from "../api";
import { PaperContext } from "../components/PaperContext";
import { PaperRow } from "../components/PaperRow";
import { RelationGraph } from "../components/RelationGraph";
import type { Paper, PaperNetwork, RecommendationItem, Recommendations } from "../types";
import { paperKey } from "../types";
import { paperLocation, recommendLocation, safeReturn } from "../workspace";

function scoreText(value: number | null) {
  return value == null ? "暂无" : value.toFixed(2);
}

function RelationLabel({ item }: { item: RecommendationItem }) {
  return <span className="relation-labels">
    {item.relations.includes("cites") ? <span className="relation relation-cites">引用关系</span> : null}
    {item.relations.includes("similar") ? <span className="relation relation-similar">主题相似 · 算法推算</span> : null}
    {!item.relations.length ? <span className="relation relation-weak">弱关联</span> : null}
  </span>;
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
    <header className="page-head"><div><h1 className="page-title">相关推荐</h1><p className="page-sub">从一篇已入库论文出发，查找相关工作。推荐会区分有来源记录的引用关系与算法推算的主题相似。</p></div></header>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
    {!choices && !error ? <Skeleton active /> : null}
    {choices && !choices.length ? <Empty description={<>文献库还没有论文，<Link to="/">先检索并加入一篇</Link>。</>} /> : null}
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

  const openPaper = useCallback((paperId: string) => { navigate(paperLocation(paperId, current)); }, [current, navigate]);

  useEffect(() => {
    document.title = recommendations ? `${recommendations.paper_title} - 相关推荐 - 文献研读` : "相关推荐 - 文献研读";
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
  const retry = () => setRequest(r => ({ seq: r.seq + 1, refresh: false }));
  return <>
    <PaperContext id={id} from={from} />
    <main className="page page-wide recommend-page">
      <header className="page-head">
        <div>
          <h1 className="page-title">相关推荐</h1>
          <p className="page-sub">{recommendations ? <>起点：<Link to={paperLocation(id, from)}>{recommendations.paper_title}</Link></> : "正在读取起点论文…"}</p>
        </div>
        <div className="page-actions"><Button icon={<ReloadOutlined />} disabled={loading} onClick={() => setRequest(r => ({ seq: r.seq + 1, refresh: true }))}>重新计算</Button></div>
      </header>
      {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={retry}>重试</Button>} /> : null}
      {warnings.map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
      <div className="recommend-layout">
        <section className="recommend-list" aria-label="推荐列表" aria-busy={loading}>
          {recommendations ? <details className="explain">
            <summary>推荐分数如何计算</summary>
            <p>{recommendations.formula}</p>
            <p>{recommendations.note}</p>
            <p className="muted">算法 {recommendations.algorithm_version} · 计算于 {new Date(recommendations.computed_at).toLocaleString("zh-CN")}</p>
          </details> : null}
          {loading && !recommendations ? <div className="row-skeletons">{[0, 1, 2].map(i => <Skeleton key={i} active paragraph={{ rows: 2 }} />)}</div> : null}
          {recommendations && !recommendations.items.length ? <Empty description="没有找到可推荐的论文" /> : null}
          <ol className="recommend-items">{recommendations?.items.map(item => <li key={item.id} className="recommend-item">
            <div className="recommend-top"><RelationLabel item={item} /><span className="muted">{item.year ?? "年份未知"}</span></div>
            <h3 className="paper-title"><Link to={paperLocation(item.id, current)}>{item.title}</Link></h3>
            <div className="strength" aria-label={`关联强度 ${item.strength}`}>
              <span className="strength-bar"><i style={{ width: `${Math.max(4, Math.min(100, item.strength * 100))}%` }} /></span>
              <span>关联强度 {item.strength}</span>
              <span className="muted">语义 {scoreText(item.semantic_score)} · 关键词 {item.keyword_score.toFixed(2)} · 引用{item.citation_score >= 1 ? "有记录" : "无记录"}</span>
            </div>
            {item.reasons.length ? <ul className="reasons">{item.reasons.map(reason => <li key={reason.type}>{reason.text}</li>)}</ul> : null}
          </li>)}</ol>
        </section>
        <section className="panel network-panel" aria-labelledby="network-title">
          <h2 id="network-title">关系网络</h2>
          <p className="legend">
            <span><i className="swatch" />实线箭头：引用（Semantic Scholar 元数据）</span>
            <span><i className="swatch similar" />虚线：主题相似（算法推算，不是引用）</span>
          </p>
          {networkError ? <Alert type="error" showIcon message={networkError} action={<Button size="small" onClick={retry}>重试</Button>} /> : null}
          {!network && loading ? <Skeleton.Node active className="network-skeleton"><span /></Skeleton.Node> : null}
          {network?.nodes.length ? <RelationGraph data={network} onOpen={openPaper} /> : null}
          {network && network.nodes.length <= 1 ? <p className="muted">只有当前论文，暂无可连接的候选。</p> : null}
          <p className="muted small-print">点击节点打开对应论文，可拖动和缩放画布。</p>
        </section>
      </div>
    </main>
  </>;
}
