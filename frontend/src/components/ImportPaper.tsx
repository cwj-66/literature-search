import { useState } from "react";
import { Alert, Button, Input } from "antd";
import { CheckOutlined, PlusOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { importPaper } from "../api";
import type { Paper } from "../types";
import { paperKey } from "../types";
import { paperLocation } from "../workspace";

export function ImportButton({ paper, onSaved, primary = false }: { paper: Paper; onSaved?: (paper: Paper) => void; primary?: boolean }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<{ created: boolean } | null>(paper.local_id ? { created: false } : null);
  async function save() {
    setLoading(true);
    setError("");
    try {
      const result = await importPaper(paper.id);
      setSaved({ created: result.created });
      onSaved?.(result.paper);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加入失败，请重试");
    } finally {
      setLoading(false);
    }
  }
  if (saved) return <span className="saved-mark"><CheckOutlined aria-hidden />{saved.created ? "已加入文献库" : "已在文献库"}</span>;
  return <span className="import-action">
    <Button type={primary ? "primary" : "default"} size={primary ? "middle" : "small"} icon={<PlusOutlined />} loading={loading} onClick={save}>加入文献库</Button>
    {error ? <span className="inline-error" role="alert">{error}</span> : null}
  </span>;
}

export function ImportPanel({ onImported, autoFocus = false }: { onImported: () => void; autoFocus?: boolean }) {
  const [reference, setReference] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<{ paper: Paper; created: boolean } | null>(null);
  async function save() {
    if (!reference.trim()) { setError("请输入论文 ID 或链接"); return; }
    setLoading(true); setError(""); setSaved(null);
    try {
      setSaved(await importPaper(reference.trim()));
      setReference("");
      onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败，请重试");
    } finally {
      setLoading(false);
    }
  }
  return <section className="import-panel" aria-label="导入论文">
    <label htmlFor="import-reference" className="field-label">论文 ID 或链接</label>
    <Input.Search id="import-reference" autoFocus={autoFocus} value={reference} maxLength={500}
      onChange={event => { setReference(event.target.value); setError(""); }}
      placeholder="arxiv:2005.11401、DOI 或 https://arxiv.org/abs/…"
      enterButton="导入" loading={loading} onSearch={save} />
    <p className="field-help">支持 arXiv ID、DOI、Semantic Scholar ID 及对应链接。导入只保存元数据和摘要，分析需在论文页单独发起。</p>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {saved ? <Alert type="success" showIcon message={<>{saved.created ? "已导入" : "文献库中已有"}：<Link to={paperLocation(paperKey(saved.paper), "/library")}>{saved.paper.title}</Link></>} /> : null}
  </section>;
}
