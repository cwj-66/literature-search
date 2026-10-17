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
    <header className="ws-page-head">
      <div>
        <h1>论文库</h1>
        <p>这个工作区里的论文。打开后可以精读，移出后文献库里的记录仍会保留。</p>
      </div>
      <Link to="/search">去搜索论文</Link>
    </header>
    <div className="ws-tools">
      {entries.length ? <>
        <Input allowClear prefix={<SearchOutlined aria-hidden />} value={filter} onChange={event => setFilter(event.target.value)} aria-label="筛选工作区论文" placeholder="筛选标题、作者或年份" />
        <span className="small-print">{needle ? `匹配 ${shown.length} / ${entries.length} 篇` : `共 ${entries.length} 篇`}</span>
      </> : <span className="small-print">上传自己的 PDF，解析后加入当前论文库。</span>}
      <Upload className="ws-upload" accept=".pdf,application/pdf" maxCount={1} showUploadList={false} beforeUpload={file => { void onUpload(file); return false; }}>
        <Button type="primary" icon={<UploadOutlined />} loading={uploading}>{uploading ? "正在解析" : "上传论文"}</Button>
      </Upload>
    </div>
    {uploadError ? <Alert type="error" showIcon message={uploadError} /> : null}
    {uploadNote ? <Alert type="success" showIcon message={uploadNote} /> : null}
    {!entries.length ? <Empty description={<>还没有论文。可以上传 PDF，在 <Link to={`/w/${id}`}>AI 研究助手</Link> 里描述题目，或 <Link to="/search">去搜索</Link> 后收藏。</>} /> : <>
      <div className="analyze-bar">
        <Checkbox.Group value={fields} options={FIELDS.map(([value, label]) => ({ value, label }))} onChange={value => setFields(value as string[])} />
        <Segmented value={depth} onChange={value => setDepth(value as "standard" | "deep")} options={[{ value: "standard", label: "标准" }, { value: "deep", label: "深度" }]} />
        <Button type="primary" disabled={!picked.length || !fields.length} loading={analyzing} onClick={analyze}>分析所选</Button>
        <span className="small-print">已选 {picked.length} / 5</span>
      </div>
      {analyzeError ? <Alert type="error" showIcon message={analyzeError} /> : null}
      {report ? <AnalyzeResult result={report} /> : null}
      {!shown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的论文"><Button onClick={() => setFilter("")}>清除筛选</Button></Empty> : <div className="paper-list">
        {shown.map(([key, item]) => <PaperRow key={key} paper={item.paper} from={here} href={workspacePaperPath(id, key)} selected={picked.includes(key)} onOpen={() => { projects.select(id); projects.open(item.paper); }} leading={
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
