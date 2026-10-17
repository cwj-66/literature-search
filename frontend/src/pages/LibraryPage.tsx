import { useEffect, useState } from "react";
import { Alert, Button, Empty, Pagination, Spin } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import { ImportPanel } from "../components/ImportPaper";
import { ResearchDesk } from "../components/ResearchDesk";
import { PaperCard } from "../components/PaperCard";
import type { LibraryResponse } from "../types";
import { graphLocation } from "../workspace";

export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const analysisMode = params.get("view") === "analysis";
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  const setPage = (value: number) => setParams(current => { const next = new URLSearchParams(current); next.set("page", String(value)); next.delete("view"); return next; });
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<LibraryResponse | null>(null);
  useEffect(() => { document.title = "我的文献 - 文献研读"; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setData(null);
    fetchLibrary(page, controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((err: Error) => {
      if (!controller.signal.aborted) setError(err.message || "本地分析库加载失败");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, retry]);
  return <main className="wrap detail">
    <div className="page-heading"><div><span className="page-kicker">我的研究材料</span><h1>我的文献</h1><p>选一篇开始研读，在同一工作空间查看分析、发现相关推荐。</p></div><Link className="text-action" to="/">＋ 检索更多文献</Link></div>
    <nav className="collection-tools library-views" aria-label="文献集合视图"><Link aria-current="page" to={`/library?${params}`}>文献列表</Link><Link to={graphLocation(`/library?${params}`)}>集合关系图谱 →</Link><span className="meta">从已入库文献探索关系</span></nav>
    <div className="library-layout"><section className="library-content" aria-label="已入库论文">
    {loading ? <Spin aria-label="正在加载本地分析库" /> : null}
    {error ? <Alert type="error" message={error} showIcon action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} /> : null}
    {data ? <>
      <div className="list-heading"><h2>{analysisMode ? "选择待分析论文" : "已入库文献"}</h2><span className="subtle-pill">共 {data.total.toLocaleString("zh-CN")} 篇</span></div>
      {!data.papers.length ? <Empty description="本地分析库为空，请从详情页加入论文，或通过 ID / 链接导入" /> : null}
      {data.papers.map((paper) => <PaperCard key={paper.local_id} paper={paper} analysisMode={analysisMode} />)}
      {data.total > 20 ? <Pagination current={page} pageSize={20} total={data.total} showSizeChanger={false} onChange={setPage} /> : null}
    </> : null}
    </section><aside className="library-sidebar"><ImportPanel onImported={() => { setPage(1); setRetry((value) => value + 1); }} />
      {data ? <ResearchDesk papers={data.papers} /> : null}
      <section className="surface guide-panel"><span className="page-kicker">开始研读</span><h2>从一篇文献出发</h2><p>打开文献后，可在阅读与分析、相关推荐之间切换。左侧保留文献列表，随时继续阅读其他论文。</p><Link to={graphLocation(`/library?${params}`)}>查看这批文献的关系 →</Link><Link to="/graph?mode=example">体验 RAG 示例图谱 →</Link></section>
    </aside></div>
  </main>;
}
