import { pageTitle } from "../presentation";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Select, Skeleton } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import { useLocation, useSearchParams } from "react-router-dom";
import { ALL_TONES, AnalysisPanel, EvidenceList, SkimPanel, type EvidenceView, type Skim } from "../components/AnalysisPanel";
import { CollectButton } from "../components/CollectButton";
import { findKey, projects, STATUS_LABEL, useProjects, type ReadStatus } from "../projects";
import { PaperContext } from "../components/PaperContext";
import { ExternalLinks, paperFacts } from "../components/PaperRow";
import { fetchPaper } from "../api";
import { useMediaQuery } from "../hooks";
import type { AnalysisResult, Paper } from "../types";
import { SOURCE_LABEL } from "../types";
import { safeReturn } from "../workspace";

function Authors({ authors }: { authors: string[] }) {
  const [open, setOpen] = useState(false);
  if (!authors.length) return <p className="paper-authors muted">作者未知</p>;
  const limit = 8;
  return <p className="paper-authors">
    {(open ? authors : authors.slice(0, limit)).join(", ")}
    {authors.length > limit ? <> <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(v => !v)}>{open ? "收起" : `等 ${authors.length} 位作者`}</button></> : null}
  </p>;
}

function Identifiers({ paper }: { paper: Paper }) {
  const items = [
    paper.arxiv_id ? <a key="arxiv" href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noopener noreferrer">arXiv {paper.arxiv_id}</a> : null,
    paper.doi ? <a key="doi" href={`https://doi.org/${paper.doi}`} target="_blank" rel="noopener noreferrer">DOI {paper.doi}</a> : null,
  ].filter(Boolean);
  return <p className="paper-ids">{items}<ExternalLinks paper={paper} /></p>;
}

export default function PaperPage() {
  const [params] = useSearchParams();
  const location = useLocation();
  const id = params.get("id")?.trim() ?? "";
  const back = safeReturn(params.get("from"));
  const wide = useMediaQuery("(min-width: 1180px)");
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [skim, setSkim] = useState<Skim>({ on: true, tones: ALL_TONES });
  const { current } = useProjects();
  const projectKey = paper ? findKey(current, paper) : "";
  useEffect(() => { if (paper) projects.open(paper); }, [paper?.id, paper?.local_id]);
  const refreshPaper = useCallback(() => {
    if (!id) return;
    fetchPaper(id).then(data => {
      setPaper(current => current ? data : current);
      window.dispatchEvent(new Event("library-changed"));
    }).catch(() => undefined);
  }, [id]);

  useEffect(() => { document.title = pageTitle(paper ? `${paper.title} - 文献检索` : "论文 - 文献检索"); }, [paper]);
  useEffect(() => {
    if (paper && location.hash === "#analysis") document.getElementById("analysis")?.scrollIntoView({ block: "start" });
  }, [paper, location.hash]);
  useEffect(() => { setEvidence(null); }, [id, wide]);

  useEffect(() => {
    setPaper(null);
    if (!id) { setLoading(false); setError("缺少论文编号"); return; }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetchPaper(id, controller.signal)
      .then(data => setPaper(data))
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || "论文详情加载失败");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, retry]);

  const aside = wide && Boolean(result);
  return <>
    {id ? <PaperContext id={id} from={back} /> : null}
    <main className={`page paper-page${aside ? " has-aside" : ""}`}>
      {loading ? <div className="reading-column"><Skeleton active title={{ width: "80%" }} paragraph={{ rows: 6 }} /></div> : null}
      {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
      {paper ? <div className="paper-layout">
        <article className={`reading-column${skim.on ? skim.tones.map(t => ` skim-${t}`).join("") : ""}`}>
          <header className="paper-header">
            <p className="paper-kicker">{paper.sources.map(s => SOURCE_LABEL[s] ?? s).join(" · ") || "论文"}</p>
            <h1 className="paper-heading">{paper.title}</h1>
            <Authors authors={paper.authors} />
            <p className="paper-facts">{paperFacts(paper).map(item => <span key={item}>{item}</span>)}</p>
            <Identifiers paper={paper} />
            <div className="paper-header-actions">
              <CollectButton key={`${paper.id}-${paper.local_id ?? ""}`} size="middle" paper={paper} onSaved={saved => setPaper(current => current?.id === paper.id ? saved : current)} />
              {projectKey ? <Select size="small" aria-label="阅读状态" value={current!.papers[projectKey].status} onChange={(v: ReadStatus) => projects.setStatus(projectKey, v)}
                options={(Object.keys(STATUS_LABEL) as ReadStatus[]).map(v => ({ value: v, label: STATUS_LABEL[v] }))} style={{ width: 84 }} /> : null}
              {paper.imported_at ? <span className="muted">入库于 {new Date(paper.imported_at).toLocaleDateString("zh-CN")}</span> : null}
            </div>
          </header>
          <section className="reading-section" aria-labelledby="abstract-title">
            <h2 id="abstract-title">摘要</h2>
            <p className="reading-text">{paper.abstract || <span className="muted">该论文没有可用摘要。</span>}</p>
          </section>
          {result && !aside ? <SkimPanel result={result} value={skim} onChange={setSkim} /> : null}
          <AnalysisPanel paperId={id} onImported={refreshPaper} mode={aside ? "aside" : "inline"} active={evidence?.key ?? ""} onEvidence={setEvidence} onResult={setResult} />
        </article>
        {aside && result ? <aside className="evidence-pane" id="evidence-pane" aria-label="速读与原文依据" aria-live="polite">
          <SkimPanel result={result} value={skim} onChange={setSkim} />
          <div className="evidence-pane-head">
            <h2>原文依据</h2>
            {evidence ? <Button type="text" size="small" icon={<CloseOutlined />} aria-label="关闭依据" onClick={() => setEvidence(null)} /> : null}
          </div>
          {evidence ? <>
            <p className="evidence-section">{evidence.section}</p>
            <p className="evidence-claim">{evidence.claim}</p>
            <EvidenceList view={evidence} />
          </> : <p className="empty-hint">点击分析结论下方的“原文依据”，在这里对照引文与所在段落。{result?.analysis_basis === "abstract" ? "本次为摘要级分析，依据均来自摘要。" : ""}</p>}
        </aside> : null}
      </div> : null}
    </main>
  </>;
}
