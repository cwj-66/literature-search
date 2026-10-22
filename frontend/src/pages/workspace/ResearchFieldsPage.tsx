import { pageTitle, displayProse } from "../../presentation";
import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, Skeleton } from "antd";
import { ArrowRightOutlined, BulbOutlined, ClockCircleOutlined, FileTextOutlined, FundProjectionScreenOutlined, ReloadOutlined, SearchOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { workspacePaperPath } from "../../workspace";
import { prepareWorkspace } from "../../api";
import { fetchWorkspaceFields, fieldActive, identifyExistingFields, startFieldAnalysis, type FieldEvidence, type FieldObservation, type FieldReport, type ResearchFieldItem, type WorkspaceFields } from "../../researchFields";
import { MissingWorkspace, useWorkspaceProject } from "./shared";
import "../../styles/research-fields.css";

export default function ResearchFieldsPage() {
  const { id, project } = useWorkspaceProject();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<WorkspaceFields | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const papersKey = Object.keys(project?.papers ?? {}).sort().join(",");
  useEffect(() => { document.title = pageTitle(`${project?.name ?? "工作区"} · 研究领域`); }, [project?.name]);
  useEffect(() => {
    if (!project) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    setError("");
    async function load() {
      try {
        const value = await fetchWorkspaceFields(id, abort.signal);
        if (abort.signal.aborted) return;
        setData(value); setError("");
        if (value.pending_analysis_count || value.identifying_count || value.fields.some(f => fieldActive(f.analysis.status))) timer = setTimeout(() => void load(), 3000);
      } catch (err) { if (!abort.signal.aborted) setError(err instanceof Error ? err.message : "加载未完成"); }
    }
    prepareWorkspace(id, project.name, papersKey ? papersKey.split(",") : [], abort.signal).then(() => load()).catch(err => { if (!abort.signal.aborted) setError(err instanceof Error ? err.message : "工作区尚未同步"); });
    return () => { abort.abort(); if (timer) clearTimeout(timer); };
  }, [id, project?.name, papersKey, revision]);
  if (!project) return <MissingWorkspace />;
  const visible = (data?.fields ?? []).filter(f => `${f.name} ${f.canonical_name}`.toLowerCase().includes(query.toLowerCase()));
  const active = visible.find(f => f.id === params.get("field")) ?? visible[0];
  async function run(field: ResearchFieldItem, refresh = false) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const state = await startFieldAnalysis(id, field.id, refresh);
      setData(current => current ? { ...current, fields: current.fields.map(f => f.id === field.id ? { ...f, analysis: state } : f) } : current);
      setRevision(r => r + 1);
    } catch (err) { setError(err instanceof Error ? err.message : "启动未完成，请重试"); }
    finally { setBusy(false); }
  }
  async function identify() {
    setBusy(true); setError("");
    try { await identifyExistingFields(id); setRevision(r => r + 1); }
    catch (err) { setError(err instanceof Error ? err.message : "识别未完成"); }
    finally { setBusy(false); }
  }
  return <main className="ws-page fields-page">
    <header className="fields-page-head"><div><h1>从一篇论文，看见一个领域。</h1><p>自动发现研究领域，按需探索演进脉络、近期热点与潜在方向。</p></div><Button icon={<ReloadOutlined />} onClick={() => setRevision(r => r + 1)}>刷新列表</Button></header>
    {error ? <Alert type="error" message={error} showIcon action={<Button size="small" onClick={() => setRevision(r => r + 1)}>重试</Button>} /> : null}
    {data?.legacy_count ? <div className="fields-legacy"><span>还有 {data.legacy_count} 篇论文可补充领域标签。已有领域报告不受影响。</span><Button loading={busy} onClick={() => void identify()}>补充领域标签</Button></div> : null}
    {data?.pending_analysis_count || data?.identifying_count ? <p className="fields-pending" role="status"><span className="fields-pulse" />正在准备领域标签，完成后会自动更新。</p> : null}
    {!data && !error ? <Skeleton active paragraph={{ rows: 10 }} /> : !data?.fields.length ? <div className="fields-empty"><FundProjectionScreenOutlined /><h2>让研究从这里展开</h2><p>论文完成分析后，这里会自动汇总它们所属的 1–3 个研究领域。<br />领域演进仅在你点击“开始分析”时生成。</p><Link to={`/w/${id}/library`}>前往文献库 <ArrowRightOutlined /></Link></div> : <div className="fields-layout">
      <aside className="fields-browser"><div className="fields-browser-heading"><h2>研究领域 <span>{data.fields.length}</span></h2><small>当前工作区 · 论文可归属多个领域</small></div><Input prefix={<SearchOutlined />} allowClear placeholder="查找领域" aria-label="查找领域" value={query} onChange={e => setQuery(e.target.value)} />
        <div className="fields-list">{visible.map(f => <button key={f.id} className={`field-card${active?.id === f.id ? " is-active" : ""}`} aria-pressed={active?.id === f.id} onClick={() => setParams({ field: f.id })}><span className="field-card-top"><i><FundProjectionScreenOutlined /></i><em>{fieldActive(f.analysis.status) ? "分析中" : f.analysis.report ? "已有报告" : "尚未分析"}</em></span><b>{f.name}</b><span className="field-card-en">{f.canonical_name}</span><span className="field-card-bottom"><FileTextOutlined /> {f.papers.length} 篇相关论文 <ArrowRightOutlined /></span></button>)}{!visible.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配领域" /> : null}</div>
      </aside>
      {active ? <section className="field-content" key={active.id}>
        <header className="field-report-head"><div><h2>{active.name}</h2><p>{active.canonical_name}{active.parent ? ` · 上级领域：${active.parent}` : ""}</p></div><Button type="primary" icon={active.analysis.report ? <ReloadOutlined /> : <ThunderboltOutlined />} loading={busy || fieldActive(active.analysis.status)} onClick={() => void run(active, Boolean(active.analysis.report))}>{active.analysis.report ? "重新分析" : "开始分析"}</Button></header>
        <div className="field-members"><span><FileTextOutlined /> 工作区关联</span>{active.papers.map(p => <Link key={p.id} title={p.title} to={workspacePaperPath(id,p.id,location.pathname+location.search)}>{p.title}</Link>)}</div>
        {fieldActive(active.analysis.status) ? <div className="field-job" role="status"><span className="fields-pulse" /><div><b>{active.analysis.stage || "准备研究任务"}</b><p>正在从公开学术 API 收集和核对论文依据。可以离开页面，任务会继续保存。</p></div></div> : null}
        {active.analysis.error ? <Alert showIcon type="warning" message={active.analysis.error} /> : null}
        {active.analysis.stale ? <p className="fields-pending">这是上一年度保存的报告，可重新分析以纳入新论文。</p> : null}
        {active.analysis.report ? <Report report={active.analysis.report} /> : !fieldActive(active.analysis.status) ? <div className="field-start"><div className="field-start-orbit"><ClockCircleOutlined /></div><h3>把研究脉络，串成一条时间线</h3><p>检索奠基工作、关键突破和近期论文，核对每条结论的摘要依据。<br />分析结果按领域保存，相同领域在其他工作区可以直接复用。</p><div><span>01 代表性文献</span><i>→</i><span>02 演进时间线</span><i>→</i><span>03 潜在方向</span></div></div> : null}
      </section> : null}
    </div>}
  </main>;
}

function Sources({ evidence, report }: { evidence: FieldEvidence[]; report: FieldReport }) {
  return <details className="field-evidence"><summary>查看论文依据 · {evidence.length}</summary>{evidence.map((e, i) => { const p = report.papers.find(p => p.id === e.paper_id); return <div key={i}><span>{p?.url && /^https?:\/\//i.test(p.url) ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.title} ↗</a> : p?.title ?? e.paper_id}</span><blockquote>{e.quote}</blockquote></div>; })}</details>;
}
function Report({ report }: { report: FieldReport }) {
  const [tab, setTab] = useState("timeline");
  return <div className="field-report"><div className="field-report-meta"><span>已保存 · 跨工作区复用</span><span>{new Date(report.generated_at).toLocaleDateString("zh-CN")}</span></div><p className="field-summary">{displayProse(report.summary)}</p>
    <nav className="field-view-tabs" aria-label="领域报告视图">{[["timeline", "演进时间线", report.milestones.length], ["hotspots", "近期热点", report.hotspots.length], ["directions", "潜在研究方向", report.directions.length]].map(([key, label, count]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(String(key))}>{label}<span>{count}</span></button>)}</nav>
    {tab === "timeline" ? <div className="field-timeline">{report.milestones.map((m, i) => { const p = report.papers.find(p => p.id === m.paper_id); return <article className="field-timeline-stop" key={m.paper_id}><div className="field-timeline-year">{i === 0 || report.milestones[i - 1].year !== m.year ? m.year : ""}</div><span className={`field-timeline-dot is-${m.kind}`} /><div className="field-milestone"><div className="field-milestone-top"><span>{{ foundation: "奠基工作", breakthrough: "方法突破", representative: "代表性研究" }[m.kind]}</span><small>{p?.venue || "公开论文"}</small></div><h3>{p?.url && /^https?:\/\//i.test(p.url) ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.title} ↗</a> : p?.title ?? m.paper_id}</h3><p>{displayProse(m.contribution)}</p><div className="field-significance"><ThunderboltOutlined /><span><b>脉络意义 · 模型归纳</b>{displayProse(m.significance)}</span></div><Sources evidence={m.evidence} report={report} /></div></article>; })}</div> : <div className="field-observations">{(tab === "hotspots" ? report.hotspots : report.directions).map((o: FieldObservation, i) => <article className={`field-observation ${tab}`} key={i}><span className="field-observation-number">{tab === "hotspots" ? <ThunderboltOutlined /> : <BulbOutlined />} 0{i + 1}</span><h3>{o.title}</h3><p>{displayProse(o.description)}</p>{o.uncertainty ? <div className="field-uncertainty"><b>研究假设与不确定性</b><p>{displayProse(o.uncertainty)}</p></div> : <small>基于 {report.coverage.recent_year_from}–{report.coverage.year_to} 年检索样本的观察</small>}<Sources evidence={o.evidence} report={report} /></article>)}{!(tab === "hotspots" ? report.hotspots.length : report.directions.length) ? <Empty description="本次样本没有足够的近期证据，未生成这部分结论。" /> : null}</div>}
    <details className="field-coverage"><summary>检索范围与分析说明 · {report.coverage.candidate_count} 篇候选 / {report.coverage.analyzed_count} 篇纳入分析</summary><p>样本年份：{report.coverage.year_from}–{report.coverage.year_to}。仅代表本次检索，不是全领域发文统计。</p><ul>{report.limitations.map((s, i) => <li key={i}>{displayProse(s)}</li>)}</ul><h4>本次检索词</h4>{report.coverage.queries.map((q, i) => <span className="field-query" key={i}>{q.query} · {q.returned} 条</span>)}</details>
  </div>;
}
