import { useEffect, useState } from "react";
import { Alert, Button, Empty, Pagination, Spin } from "antd";
import { Link } from "react-router-dom";
import { fetchLibrary } from "../api";
import { ImportPanel } from "../components/ImportPaper";
import { PaperCard } from "../components/PaperCard";
import type { LibraryResponse } from "../types";

export default function LibraryPage() {
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<LibraryResponse | null>(null);
  useEffect(() => { document.title = "本地分析库 - 文献检索"; }, []);
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
    <div className="card-links"><Link to="/">返回检索</Link><Link to="/graph">知识图谱</Link></div><h1>本地分析库</h1>
    <p>已入库论文可在后端重启或数据源不可用时继续查看，重复导入会合并已有记录。</p>
    <ImportPanel onImported={() => { setPage(1); setRetry((value) => value + 1); }} />
    {loading ? <Spin aria-label="正在加载本地分析库" /> : null}
    {error ? <Alert type="error" message={error} showIcon action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} /> : null}
    {data ? <>
      <p>已入库 {data.total.toLocaleString("zh-CN")} 篇</p>
      {!data.papers.length ? <Empty description="本地分析库为空，请从详情页加入论文，或通过 ID / 链接导入" /> : null}
      {data.papers.map((paper) => <PaperCard key={paper.local_id} paper={paper} />)}
      {data.total > 20 ? <Pagination current={page} pageSize={20} total={data.total} showSizeChanger={false} onChange={setPage} /> : null}
    </> : null}
  </main>;
}
