import { useEffect, useState } from "react";
import { Alert, Button, Empty, Pagination, Spin } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import { ImportPanel } from "../components/ImportPaper";
import { PaperCard } from "../components/PaperCard";
import type { LibraryResponse } from "../types";

export default function LibraryPage() {
  const [params] = useSearchParams();
  const analysisMode = params.get("view") === "analysis";
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<LibraryResponse | null>(null);
  useEffect(() => { document.title = `${analysisMode ? "论文分析" : "论文库"} - 文献研读`; }, [analysisMode]);
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
    <div className="page-heading"><div><span className="page-kicker">{analysisMode ? "阅读与提取" : "我的研究材料"}</span><h1>{analysisMode ? "论文分析" : "论文库"}</h1><p>{analysisMode ? "选择一篇论文，阅读摘要、提取研究内容，并逐条核对原文依据。" : "集中管理已入库论文，让阅读、分析与知识探索接续进行。"}</p></div><Link className="text-action" to={analysisMode ? "/library" : "/graph"}>{analysisMode ? "管理论文库 →" : "查看知识图谱 →"}</Link></div>
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
      <section className="surface guide-panel"><span className="page-kicker">下一步</span><h2>先阅读，再判断。</h2><p>在论文详情中触发 AI 分析，查看研究问题、技术步骤和作者贡献。重要结论可展开原文核对。</p><Link to="/recommend">从论文出发找相似文献 →</Link><Link to="/graph">在知识图谱中查看主题联系 →</Link></section>
    </aside></div>
  </main>;
}
