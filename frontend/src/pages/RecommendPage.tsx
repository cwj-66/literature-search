import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Spin, Tag } from "antd";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { fetchNetwork, fetchRecommendations } from "../api";
import { RelationGraph } from "../components/RelationGraph";
import type { PaperNetwork, Recommendations } from "../types";

function scoreText(value: number | null) {
  return value == null ? "暂无" : value.toFixed(2);
}

export default function RecommendPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const id = params.get("id")?.trim() ?? "";
  const [request, setRequest] = useState({ seq: 0, refresh: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [networkError, setNetworkError] = useState("");
  const [recommendations, setRecommendations] = useState<Recommendations | null>(null);
  const [network, setNetwork] = useState<PaperNetwork | null>(null);

  const openPaper = useCallback((paperId: string) => {
    const from = `/recommend?id=${encodeURIComponent(id)}`;
    navigate(`/paper?${new URLSearchParams({ id: paperId, from })}`);
  }, [id, navigate]);

  useEffect(() => {
    document.title = recommendations
      ? `${recommendations.paper_title} - 相似推荐 - 文献检索`
      : "相似推荐 - 文献检索";
  }, [recommendations]);

  useEffect(() => {
    if (!id) {
      setLoading(false);
      setError("缺少论文编号");
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
  return (
    <main className="wrap detail">
      <div className="card-links">
        {id ? <Link to={`/paper?id=${encodeURIComponent(id)}`}>返回论文</Link> : <Link to="/">返回检索</Link>}
        <Link to="/library">本地分析库</Link>
      </div>
      <h1>相似推荐</h1>
      {recommendations ? <p className="meta">{recommendations.paper_title}</p> : null}
      {loading ? <div style={{ padding: 32, textAlign: "center" }}><Spin tip="正在计算关联强度" /></div> : null}
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
                <Link to={`/paper?${new URLSearchParams({ id: item.id, from: `/recommend?id=${encodeURIComponent(id)}` })}`}>{item.title}</Link>
              </h2>
              <div className="strength">关联强度 {item.strength}</div>
              <div className="meta">
                语义 {scoreText(item.semantic_score)} · 关键词 {item.keyword_score.toFixed(2)} · 引用 {item.citation_score >= 1 ? "有记录" : "无记录"}
              </div>
              {item.reasons.map((reason) => <p key={reason.type} className="meta">{reason.text}</p>)}
              <div className="card-links">
                <Link to={`/paper?${new URLSearchParams({ id: item.id, from: `/recommend?id=${encodeURIComponent(id)}` })}`}>详情</Link>
              </div>
            </article>
          ))}
        </>
      ) : null}
      {id ? (
        <>
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
          {network ? <RelationGraph data={network} onOpen={openPaper} /> : null}
          {network && network.nodes.length <= 1 ? <p className="meta">当前只有这篇论文，没有可连接的候选。</p> : null}
        </>
      ) : null}
    </main>
  );
}
