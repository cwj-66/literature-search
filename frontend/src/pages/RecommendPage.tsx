import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Spin, Tag } from "antd";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { fetchLibrary, fetchNetwork, fetchRecommendations } from "../api";
import { RelationGraph } from "../components/RelationGraph";
import type { Paper, PaperNetwork, Recommendations } from "../types";
import { PaperViews } from "../components/ResearchWorkspace";
import { paperLocation, safeReturn } from "../workspace";

function scoreText(value: number | null) {
  return value == null ? "暂无" : value.toFixed(2);
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
  const [choices, setChoices] = useState<Paper[]>([]);
  const [choosing, setChoosing] = useState(false);
  const [choiceError, setChoiceError] = useState("");
  useEffect(() => {
    if (id) return;
    const controller = new AbortController(); setChoosing(true); setChoiceError("");
    fetchLibrary(1, controller.signal).then(data => { if (!controller.signal.aborted) setChoices(data.papers); })
      .catch(err => { if (!controller.signal.aborted) setChoiceError(err.message || "论文库加载失败"); })
      .finally(() => { if (!controller.signal.aborted) setChoosing(false); });
    return () => controller.abort();
  }, [id, request.seq]);

  const openPaper = useCallback((paperId: string) => {
    navigate(paperLocation(paperId, current));
  }, [current, navigate]);

  useEffect(() => {
    document.title = recommendations
      ? `${recommendations.paper_title} - 相似推荐 - 文献检索`
      : "相似推荐 - 文献检索";
  }, [recommendations]);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      setError("");
      setRecommendations(null);
      setNetwork(null);
      return;
    }
    const controller = new AbortController();
    const refresh = request.refresh;
    setLoading(true);
    setError("");
    setNetworkError("");
    setRecommendations(null);
    setNetwork(null);
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
        fetchRecommendations(id, false, controller.signal).then((result) => {
          if (!controller.signal.aborted) setRecommendations(result);
        }).catch(fail(setError, "相似推荐加载失败")),
        fetchNetwork(id, false, controller.signal).then((result) => {
          if (!controller.signal.aborted) setNetwork(result);
        }).catch(fail(setNetworkError, "关系网络加载失败")),
      ]);
    };
    load().catch(fail(setError, "相似推荐加载失败")).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [id, request]);

  const warnings = Array.from(new Set([...(recommendations?.warnings ?? []), ...(network?.warnings ?? [])]));
  if (!id) return <main className="wrap detail">
    <div className="page-heading"><div><span className="page-kicker">发现相关研究</span><h1>相似推荐</h1><p>从一篇已入库论文出发，寻找相关工作，区分主题相似与真实引用。</p></div><Link className="text-action" to="/library">管理论文库 →</Link></div>
    <div className="library-layout"><section><div className="list-heading"><h2>选择一篇起点论文</h2><span className="meta">最近入库，最多展示 20 篇</span></div>
      {choosing ? <Spin aria-label="正在加载起点论文" /> : null}
      {choiceError ? <Alert type="error" message={choiceError} action={<Button onClick={() => setRequest(r => ({ ...r, seq: r.seq + 1 }))}>重试</Button>} /> : null}
      {!choosing && !choiceError && !choices.length ? <Empty description={<span>还没有入库论文，<Link to="/library">先导入一篇论文</Link>即可开始。</span>} /> : null}
      {choices.map(p => <article className="paper-card recommendation-choice" key={p.local_id ?? p.id}><span className="paper-year">{p.year ?? "年份未知"}</span><h2 className="paper-title"><Link to={`/recommend?id=${encodeURIComponent(p.local_id ?? p.id)}`}>{p.title}</Link></h2><p className="meta">{p.authors.slice(0, 3).join(" · ") || "作者未知"}</p><Link to={`/recommend?id=${encodeURIComponent(p.local_id ?? p.id)}`}>查找相似文献 →</Link></article>)}
    </section><aside className="surface guide-panel"><span className="page-kicker">如何阅读推荐</span><h2>相似，并不等于引用。</h2><p>推荐列表说明关联原因；关系网络区分有来源的引用记录与算法推算的主题相似。</p><p>数据源不可用时会提示降级，已有缓存仍可复用。</p><Link to="/graph">探索更广的实体关系 →</Link></aside></div>
  </main>;
  return (
    <main className="wrap detail recommend-page">
      <div className="breadcrumbs">
        <Link to={from}>{from.startsWith("/library") ? "返回文献列表" : "返回研读来源"}</Link>
      </div>
      <PaperViews id={id} from={from} title={recommendations?.paper_title} />
      <div className="page-heading"><div><h1>相关推荐</h1><p>以当前论文为起点，查看相关研究与推荐依据。打开候选论文后可返回这里。</p></div></div>
      {loading ? <Spin tip="正在计算关联强度"><div style={{ height: 100 }} /></Spin> : null}
      {error ? (
        <Alert
          style={{ marginTop: 16 }}
          type="error"
          showIcon
          message={error}
          action={<Button size="small" onClick={() => setRequest((current) => ({ seq: current.seq + 1, refresh: false }))}>重试</Button>}
        />
      ) : null}
      {recommendations ? (
        <>
          <Alert style={{ marginTop: 16 }} type="info" showIcon message="分数说明" description={
            <>
              <div>{recommendations.formula}</div>
              <div>{recommendations.note}</div>
              <div className="meta">算法 {recommendations.algorithm_version} · {new Date(recommendations.computed_at).toLocaleString("zh-CN")}</div>
            </>
          } />
          {warnings.map((warning) => <Alert key={warning} style={{ marginTop: 12 }} type="warning" showIcon message={warning} />)}
          <div className="analysis-actions">
            <Button onClick={() => setRequest((current) => ({ seq: current.seq + 1, refresh: true }))}>重新计算</Button>
          </div>
          <h2>推荐列表</h2>
          {!recommendations.items.length ? <Empty description="没有找到可推荐的论文" /> : null}
          {recommendations.items.map((item) => (
            <article key={item.id} className="paper-card">
              <div className="tag-row">
                <Tag>{item.year ?? "年份未知"}</Tag>
                {item.relations.includes("cites") ? <Tag color="blue">引用关系</Tag> : null}
                {item.relations.includes("similar") ? <Tag>主题相似</Tag> : null}
                {!item.relations.length ? <Tag>弱关联</Tag> : null}
              </div>
              <h2 className="paper-title">
                <Link to={paperLocation(item.id, current)}>{item.title}</Link>
              </h2>
              <div className="strength">关联强度 {item.strength}</div>
              <div className="meta">
                语义 {scoreText(item.semantic_score)} · 关键词 {item.keyword_score.toFixed(2)} · 引用 {item.citation_score >= 1 ? "有记录" : "无记录"}
              </div>
              {item.reasons.map((reason) => <p key={reason.type} className="meta">{reason.text}</p>)}
              <div className="card-links">
                <Link to={paperLocation(item.id, current)}>打开研读 →</Link>
              </div>
            </article>
          ))}
        </>
      ) : null}
      {id ? (
        <section className="surface recommendation-network">
          <h2>关系网络</h2>
          <div className="legend">
            <span><i className="swatch" />实线箭头：引用（Semantic Scholar 元数据）</span>
            <span><i className="swatch similar" />虚线：主题相似（算法推算，不是引用）</span>
            <span><b style={{ color: "#1f4b99" }}>●</b> 当前论文</span>
          </div>
          {networkError ? (
            <Alert
              type="error"
              showIcon
              message={networkError}
              action={<Button size="small" onClick={() => setRequest((current) => ({ seq: current.seq + 1, refresh: false }))}>重试</Button>}
            />
          ) : null}
          {network?.nodes.length ? <RelationGraph data={network} onOpen={openPaper} /> : null}
          {network && network.nodes.length <= 1 ? <p className="meta">当前只有这篇论文，没有可连接的候选。</p> : null}
        </section>
      ) : null}
    </main>
  );
}
