import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Pagination, Skeleton } from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import { AnalysisBadge, useAnalysisStates } from "../components/AnalysisBadge";
import { ImportPanel } from "../components/ImportPaper";
import { PaperRow } from "../components/PaperRow";
import { SelectionBar } from "../components/SelectionBar";
import { selection, useSelection } from "../selection";
import type { LibraryResponse } from "../types";
import { paperKey } from "../types";
import { graphLocation, paperLocation, recommendLocation } from "../workspace";

export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const here = location.pathname + location.search;
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  const importOpen = params.get("import") === "1";
  const patch = (values: Record<string, string | null>) => setParams(current => {
    const next = new URLSearchParams(current);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    return next;
  });
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [filter, setFilter] = useState("");
  const picked = useSelection();
  const ids = data?.papers.map(paperKey) ?? [];
  const states = useAnalysisStates(ids);
  useEffect(() => { document.title = "文献库 - 文献研读"; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    fetchLibrary(page, controller.signal).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch((err: Error) => {
      if (!controller.signal.aborted) setError(err.message || "文献库加载失败");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, retry]);
  const needle = filter.trim().toLocaleLowerCase();
  const visible = data?.papers.filter(paper => !needle || [paper.title, ...paper.authors, paper.year, paper.venue].filter(Boolean).join(" ").toLocaleLowerCase().includes(needle)) ?? [];
  const pageItems = visible.map(paper => ({ id: paperKey(paper), title: paper.title }));
  const pickedOnPage = pageItems.filter(item => picked.some(p => p.id === item.id)).length;
  const empty = data && !data.total;
  return <main className="page page-wide library-page">
    <header className="page-head">
      <div>
        <h1 className="page-title">文献库</h1>
        <p className="page-sub">{data ? `共 ${data.total.toLocaleString("zh-CN")} 篇 · 按导入时间排列` : "已收藏的论文"}</p>
      </div>
      <div className="page-actions">
        <Link to={graphLocation(here)} className="quiet-link">集合图谱</Link>
        <Button type={empty ? "primary" : "default"} icon={<PlusOutlined />} aria-expanded={importOpen || Boolean(empty)} onClick={() => patch({ import: importOpen ? null : "1" })}>导入论文</Button>
      </div>
    </header>
    {importOpen || empty ? <div className="panel import-wrap"><ImportPanel autoFocus={importOpen} onImported={() => { patch({ page: null }); setRetry(v => v + 1); }} /></div> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
    {loading && !data ? <div className="row-skeletons">{[0, 1, 2, 3].map(i => <Skeleton key={i} active title={{ width: "60%" }} paragraph={{ rows: 1 }} />)}</div> : null}
    {empty ? <Empty description={<>文献库为空。<Link to="/">检索论文</Link>后点“加入文献库”，或用上方输入框导入。</>} /> : null}
    {data && data.papers.length ? <section className="library-surface" aria-label="文献列表" aria-busy={loading}>
      <div className="library-tools">
        <Input allowClear prefix={<SearchOutlined aria-hidden />} value={filter} onChange={event => setFilter(event.target.value)} aria-label="筛选本页文献" placeholder="筛选本页标题、作者或年份" />
        <span className="small-print">{needle ? `匹配 ${visible.length} / ${data.papers.length} 篇` : `本页 ${data.papers.length} 篇`}<span className="library-tools-tip"> · 勾选后可提问或对比</span></span>
      </div>
      <div className="list-toolbar">
        <Checkbox disabled={!pageItems.length} checked={pageItems.length > 0 && pickedOnPage === pageItems.length} indeterminate={pickedOnPage > 0 && pickedOnPage < pageItems.length}
          onChange={event => selection.setMany(pageItems, event.target.checked)}>{needle ? "选择匹配文献" : "选择本页"}</Checkbox>
        {picked.length ? <span className="list-toolbar-hint">已选 {picked.length} 篇，跨页保留</span> : null}
      </div>
      <div className="paper-list library-list">
        {visible.map(paper => {
          const id = paperKey(paper);
          return <PaperRow key={id} paper={paper} from={here} abstract={false} selected={picked.some(p => p.id === id)}
            leading={<Checkbox aria-label={`选择：${paper.title}`} checked={picked.some(p => p.id === id)} onChange={() => selection.toggle({ id, title: paper.title })} />}
            side={<><Link className="analysis-status-link" aria-label={`查看分析：${paper.title}`} to={`${paperLocation(id, here)}#analysis`}><AnalysisBadge state={states[id]} /></Link><Link className="quiet-link" to={recommendLocation(id, here)}>相关推荐</Link></>} />;
        })}
        {!visible.length ? <div className="center-pad"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本页没有匹配文献"><Button onClick={() => setFilter("")}>清除筛选</Button></Empty></div> : null}
      </div>
      {data.total > 20 ? <div className="pager"><Pagination current={page} pageSize={20} total={data.total} showSizeChanger={false} onChange={value => { setFilter(""); patch({ page: value === 1 ? null : String(value) }); }} /></div> : null}
    </section> : null}
    <SelectionBar />
  </main>;
}
