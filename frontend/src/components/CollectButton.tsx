import { useState } from "react";
import { Button } from "antd";
import { BookOutlined, CheckOutlined } from "@ant-design/icons";
import { importPaper } from "../api";
import { findKey, projects, useProjects } from "../projects";
import type { Paper } from "../types";

export function CollectButton({ paper, onSaved, size = "small" }: { paper: Paper; onSaved?: (paper: Paper) => void; size?: "small" | "middle" }) {
  const { current } = useProjects();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const collected = Boolean(findKey(current, paper));
  async function save() {
    setLoading(true); setError("");
    try {
      const { paper: saved } = await importPaper(paper.id);
      projects.add(saved);
      onSaved?.(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "收藏失败，请重试");
    } finally { setLoading(false); }
  }
  if (collected) return <span className="saved-mark"><CheckOutlined aria-hidden />已在「{current!.name}」</span>;
  return <span className="import-action">
    <Button size={size} icon={<BookOutlined />} loading={loading} onClick={save}>{current ? `收藏到「${current.name}」` : "收藏并新建项目"}</Button>
    {error ? <span className="inline-error" role="alert">{error}</span> : null}
  </span>;
}
