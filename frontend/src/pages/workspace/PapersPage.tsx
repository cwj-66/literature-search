import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Popconfirm, Segmented, Upload } from "antd";
import { DeleteOutlined, SearchOutlined, UploadOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { analyzePapers, uploadPaper } from "../../api";
import { AnalyzeResult } from "../../components/AnalyzeResult";
import { PaperRow } from "../../components/PaperRow";
import { STATUS_LABEL, projects } from "../../projects";
import type { AnalyzePaperResult } from "../../types";
import { workspacePaperPath } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

const FIELDS = [
  ["research_content", "研究内容"],
  ["technical_scheme", "技术方案"],
  ["contributions", "创新点与主要贡献"],
  ["experiments", "实验结果"],
  ["limitations", "局限性"],
] as const;

export default function PapersPage() {
  const { id, project } = useWorkspaceProject();
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [fields, setFields] = useState<string[]>(FIELDS.map(item => item[0]));
  const [depth, setDepth] = useState<"standard" | "deep">("standard");
  const [report, setReport] = useState<AnalyzePaperResult | null>(null);
  const [analyzeError, setAnalyzeError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  const here = `/w/${id}/library`;
  function toggle(key: string) {
    setPicked(current => current.includes(key) ? current.filter(item => item !== key) : current.length >= 5 ? current : [...current, key]);
  }
  function analyze() {
    if (!picked.length || !fields.length || analyzing) return;
    setAnalyzing(true); setAnalyzeError("");
    setReport(null);
    analyzePapers({ paper_ids: picked, fields, depth, workspace_id: id })
      .then(setReport)
      .catch((err: Error) => setAnalyzeError(err.message || "分析失败，请重试"))
      .finally(() => setAnalyzing(false));
  }
  async function onUpload(file: File) {
    if (uploading) return;
    setUploading(true); setUploadError(""); setUploadNote("");
    try {
      const result = await uploadPaper(file, id);
      projects.select(id);
      projects.add(result.paper);
      if (result.index.status === "completed") setUploadNote(`已解析并写入向量库，共 ${result.index.chunk_count} 段`);
      else setUploadError(result.index.message || "向量入库失败");
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }
  useEffect(() => { document.title = project ? `${project.name} - 论文库` : "工作区"; }, [project]);
  if (!project) return <MissingWorkspace />;
  const entries = Object.entries(project.papers).sort((a, b) => (b[1].openedAt ?? b[1].addedAt).localeCompare(a[1].openedAt ?? a[1].addedAt));
  const needle = filter.trim().toLocaleLowerCase();
  const shown = entries.filter(([, item]) => !needle || [item.paper.title, ...item.paper.authors, item.paper.year, item.paper.venue].filter(Boolean).join(" ").toLocaleLowerCase().includes(needle));
  return <main className="ws-page ws-library">
    <header className="ws-page-head lib-head">
      <div>
        <h1>论文库</h1>
        <p>在当前工作区中添加的论文，可以进行精读、分析，并用于 AI 问答。</p>
      </div>
      {entries.length ? <Link to="/search">去搜索论文</Link> : null}
    </header>
    {entries.length ? <div className="ws-tools">
      <Input allowClear prefix={<SearchOutlined aria-hidden />} value={filter} onChange={event => setFilter(event.target.value)} aria-label="筛选工作区论文" placeholder="筛选标题、作者或年份" />
      <span className="small-print">{needle ? `匹配 ${shown.length} / ${entries.length} 篇` : `共 ${entries.length} 篇`}</span>
      <Upload className="ws-upload" accept=".pdf,application/pdf" maxCount={1} showUploadList={false} beforeUpload={file => { void onUpload(file); return false; }}>
        <Button type="primary" icon={<UploadOutlined />} loading={uploading}>{uploading ? "正在解析" : "上传论文"}</Button>
      </Upload>
    </div> : null}
    {uploadError ? <Alert type="error" showIcon message={uploadError} /> : null}
    {uploadNote ? <Alert type="success" showIcon message={uploadNote} /> : null}
    {!entries.length ? <section className="lib-empty">
      <svg className="lib-art" viewBox="0 0 220 140" aria-hidden="true">
        <ellipse cx="112" cy="92" rx="64" ry="16" fill="#e7f0ff" />
        <path d="M36 78c30-36 96-42 148-6" fill="none" stroke="#d5e4f8" strokeWidth="3" strokeLinecap="round" />
        <circle cx="42" cy="72" r="3.5" fill="#d7e5fb" />
        <circle cx="178" cy="64" r="2.5" fill="#d7e5fb" />
        <rect x="78" y="32" width="64" height="80" rx="10" fill="#fff" stroke="#d5e3f7" />
        <rect x="90" y="48" width="36" height="5" rx="2.5" fill="#c5d7f4" />
        <rect x="90" y="60" width="28" height="4" rx="2" fill="#e4edf8" />
        <rect x="90" y="70" width="32" height="4" rx="2" fill="#e4edf8" />
        <circle cx="128" cy="96" r="15" fill="#3d6ad6" />
        <path d="M128 89v14M121 96h14" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
      <h2>还没有论文</h2>
      <p>把论文加入当前工作区后，你可以进行精读、问答、比较和自动分析。</p>
      <div className="lib-actions">
        <Link className="lib-btn lib-btn-primary" to="/search"><SearchOutlined aria-hidden />搜索并添加论文</Link>
        <Upload accept=".pdf,application/pdf" maxCount={1} showUploadList={false} beforeUpload={file => { void onUpload(file); return false; }}>
          <button type="button" className="lib-btn" disabled={uploading}><UploadOutlined aria-hidden />{uploading ? "正在解析" : "上传 PDF"}</button>
        </Upload>
      </div>
      <div className="lib-points">
        <article><span className="lib-ico is-blue" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 4.5h6.4L18 9v10.2a1.2 1.2 0 0 1-1.2 1.2H7A1.2 1.2 0 0 1 5.8 19.2V5.7A1.2 1.2 0 0 1 7 4.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M13.2 4.6V9H18" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg></span><strong>自动解析</strong><span>自动提取论文信息，识别标题、作者、摘要等。</span></article>
        <article><span className="lib-ico is-violet" aria-hidden="true"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="7" rx="6" ry="2.4" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M6 7v5c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4V7M6 12v5c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4v-5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg></span><strong>建立索引</strong><span>对论文内容建立语义索引，支持高效检索与定位。</span></article>
        <article><span className="lib-ico is-green" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M6 7.2A5.2 5.2 0 0 1 11.2 2h1.6A5.2 5.2 0 0 1 18 7.2v3.2A4.2 4.2 0 0 1 13.8 14.6H12l-3.2 2.2v-2.4A5 5 0 0 1 6 10.4Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg></span><strong>支持 AI 问答</strong><span>基于论文内容进行问答，快速获取关键信息。</span></article>
      </div>
      <p className="lib-foot">支持从搜索结果加入论文，也支持上传自己的 PDF 文件。</p>
    </section> : <>
      <div className="analyze-bar">
        <Checkbox.Group value={fields} options={FIELDS.map(([value, label]) => ({ value, label }))} onChange={value => setFields(value as string[])} />
        <Segmented value={depth} onChange={value => setDepth(value as "standard" | "deep")} options={[{ value: "standard", label: "标准" }, { value: "deep", label: "深度" }]} />
        <Button type="primary" disabled={!picked.length || !fields.length} loading={analyzing} onClick={analyze}>分析所选</Button>
        <span className="small-print">已选 {picked.length} / 5</span>
      </div>
      {analyzeError ? <Alert type="error" showIcon message={analyzeError} /> : null}
      {report ? <AnalyzeResult result={report} /> : null}
      {!shown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的论文"><Button onClick={() => setFilter("")}>清除筛选</Button></Empty> : <div className="paper-list">
        {shown.map(([key, item]) => <PaperRow key={key} paper={item.paper} from={here} href={workspacePaperPath(id, key, here)} selected={picked.includes(key)} onOpen={() => { projects.select(id); projects.open(item.paper); }} leading={
          <Checkbox aria-label={`选择：${item.paper.title}`} checked={picked.includes(key)} disabled={!picked.includes(key) && picked.length >= 5} onChange={() => toggle(key)} />
        } side={<>
          <span className={`status status-${item.status === "read" ? "success" : item.status === "reading" ? "accent" : "neutral"}`}>{STATUS_LABEL[item.status]}</span>
          <Popconfirm title="从当前工作区移出这篇论文？" description="文献库中的记录和分析会保留。" okText="移出" cancelText="取消" onConfirm={() => { projects.select(id); projects.removePaper(key); }}>
            <Button size="small" type="text" icon={<DeleteOutlined />} aria-label={`移出：${item.paper.title}`}>移出</Button>
          </Popconfirm>
        </>} />)}
      </div>}
    </>}
  </main>;
}
