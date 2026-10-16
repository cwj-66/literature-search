import { useState } from "react";
import { Alert, Button, Input } from "antd";
import { Link } from "react-router-dom";
import { importPaper } from "../api";
import type { Paper } from "../types";
import { detailPath } from "./PaperCard";

export function ImportButton({ paper, onSaved }: { paper: Paper; onSaved: (paper: Paper) => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setLoading(true);
    setError("");
    try { onSaved((await importPaper(paper.id)).paper); }
    catch (err) { setError(err instanceof Error ? err.message : "导入失败，请重试"); }
    finally { setLoading(false); }
  }
  return <div style={{ marginBottom: 16 }}>
    <Button type="primary" loading={loading} disabled={Boolean(paper.local_id)} onClick={save}>
      {paper.local_id ? "已加入本地分析库" : "加入本地分析库"}
    </Button>
    {paper.imported_at ? <p className="meta">入库时间：{new Date(paper.imported_at).toLocaleString("zh-CN")}</p> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button onClick={save} loading={loading}>重试导入</Button>} /> : null}
  </div>;
}

export function ImportPanel({ onImported }: { onImported: () => void }) {
  const [reference, setReference] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Paper | null>(null);
  async function save() {
    if (!reference.trim()) { setError("请输入论文 ID 或链接"); return; }
    setLoading(true); setError(""); setSaved(null);
    try {
      setSaved((await importPaper(reference.trim())).paper);
      onImported();
    } catch (err) { setError(err instanceof Error ? err.message : "导入失败，请重试"); }
    finally { setLoading(false); }
  }
  return <section className="paper-card" aria-label="导入论文">
    <span className="page-kicker">添加研究材料</span><h2>导入论文</h2>
    <p className="meta">支持 arXiv ID、DOI、Semantic Scholar ID 及对应论文链接。仅保存论文元数据和摘要，尚未执行 AI 分析。</p>
    <Input.Search aria-label="论文 ID 或链接" value={reference} maxLength={500}
      onChange={(event) => { setReference(event.target.value); setError(""); setSaved(null); }}
      placeholder="例如 arxiv:2005.11401 或 https://arxiv.org/abs/2005.11401"
      enterButton="导入" loading={loading} onSearch={save} />
    {error ? <Alert style={{ marginTop: 12 }} type="error" showIcon message={error}
      action={<Button loading={loading} onClick={save}>重试导入</Button>} /> : null}
    {saved ? <Alert style={{ marginTop: 12 }} type="success" showIcon message={<>
      已保存：<Link to={detailPath(saved.local_id ?? saved.id, "/library")}>{saved.title}</Link>
    </>} /> : null}
  </section>;
}
