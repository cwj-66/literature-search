import { useEffect, useState } from "react";
import { Alert, Button, Drawer, Empty, Pagination, Spin } from "antd";
import { ArrowLeftOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import type { LibraryResponse } from "../types";
import { paperKey } from "../types";
import { graphLocation, libraryLocation, paperLocation, recommendLocation, returnLabel } from "../workspace";

function LibraryDrawer({ open, onClose, current, from }: { open: boolean; onClose: () => void; current: string; from: string }) {
  const [params] = useSearchParams();
  const library = new URL(libraryLocation(params), window.location.origin);
  const [page, setPage] = useState(Math.max(1, Number(library.searchParams.get("page")) || 1));
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    fetchLibrary(page, controller.signal).then(setData).catch(err => { if (!controller.signal.aborted) setError(err.message || "文献库加载失败"); });
    return () => controller.abort();
  }, [open, page, retry]);
  return <Drawer open={open} onClose={onClose} title="切换论文" placement="left" width={380} styles={{ body: { padding: 0 } }}>
    {error ? <Alert className="drawer-alert" type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} />
      : !data ? <div className="center-pad"><Spin /></div>
      : !data.papers.length ? <Empty className="center-pad" description={<>文献库还没有论文，<Link to="/" onClick={onClose}>去检索</Link></>} />
      : <nav aria-label="文献库论文" className="drawer-list">
        {data.papers.map(paper => {
          const id = paperKey(paper);
          const active = id === current || paper.id === current;
          return <Link key={id} to={paperLocation(id, from)} onClick={onClose} aria-current={active ? "page" : undefined}>
            <strong>{paper.title}</strong>
            <span>{[paper.year, paper.authors[0]].filter(Boolean).join(" · ") || "信息缺失"}</span>
          </Link>;
        })}
        {data.total > 20 ? <Pagination simple size="small" current={page} pageSize={20} total={data.total} onChange={setPage} /> : null}
      </nav>}
  </Drawer>;
}

export function PaperContext({ id, from }: { id: string; from: string }) {
  const location = useLocation();
  const [drawer, setDrawer] = useState(false);
  const here = location.pathname + location.search;
  return <div className="paper-context">
    <div className="paper-context-inner">
      <Link to={from} className="back-link"><ArrowLeftOutlined aria-hidden /><span className="back-label">返回{returnLabel(from)}</span></Link>
      <nav className="view-tabs" aria-label="论文视图">
        <Link to={paperLocation(id, from)} aria-current={location.pathname === "/paper" ? "page" : undefined}>阅读与分析</Link>
        <Link to={recommendLocation(id, from)} aria-current={location.pathname === "/recommend" ? "page" : undefined}>相关推荐</Link>
        <Link to={graphLocation(here, id)}>在图谱中定位</Link>
      </nav>
      <Button className="context-drawer-button" icon={<UnorderedListOutlined />} onClick={() => setDrawer(true)}>文献库</Button>
    </div>
    <LibraryDrawer open={drawer} onClose={() => setDrawer(false)} current={id} from={from} />
  </div>;
}
