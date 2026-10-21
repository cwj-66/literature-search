import type { ReactNode } from "react";
import { Link } from "react-router-dom";

function CompareArt() {
  return <svg className="empty-art" viewBox="0 0 220 128" aria-hidden="true">
    <rect x="36" y="30" width="70" height="82" rx="6" fill="#fff" stroke="#cbd2dc" />
    <rect x="50" y="46" width="30" height="5" rx="1.5" fill="#24479e" />
    <rect x="50" y="58" width="42" height="3" rx="1.5" fill="#e0e4ea" />
    <rect x="50" y="67" width="34" height="3" rx="1.5" fill="#e0e4ea" />
    <rect x="50" y="76" width="38" height="3" rx="1.5" fill="#e0e4ea" />
    <rect x="114" y="16" width="70" height="82" rx="6" fill="#fff" stroke="#cbd2dc" />
    <rect x="128" y="32" width="30" height="5" rx="1.5" fill="#6a4bb0" />
    <rect x="128" y="44" width="42" height="3" rx="1.5" fill="#e0e4ea" />
    <rect x="128" y="53" width="34" height="3" rx="1.5" fill="#e0e4ea" />
    <rect x="128" y="62" width="38" height="3" rx="1.5" fill="#e0e4ea" />
    <path d="M92 64h36" stroke="#b7c5e6" strokeDasharray="3 4" />
  </svg>;
}

function GraphArt() {
  return <svg className="empty-art" viewBox="0 0 240 140" aria-hidden="true">
    <path d="M120 70 58 40M120 70 182 40M120 70 52 106M120 70 188 106" fill="none" stroke="#b7c5e6" strokeWidth="1.4" />
    <path d="M182 40 188 106" fill="none" stroke="#0f7c74" strokeWidth="1.2" strokeDasharray="5 4" opacity=".6" />
    <circle cx="58" cy="40" r="9" fill="#e8edf8" stroke="#24479e" strokeWidth="1.4" />
    <circle cx="182" cy="40" r="9" fill="#e8edf8" stroke="#24479e" strokeWidth="1.4" />
    <circle cx="52" cy="106" r="7" fill="#f1edf8" stroke="#6a4bb0" strokeWidth="1.4" />
    <circle cx="188" cy="106" r="7" fill="#ebf5ee" stroke="#2f8650" strokeWidth="1.4" />
    <circle cx="120" cy="70" r="20" fill="#24479e" />
    <circle cx="120" cy="70" r="15" fill="none" stroke="#fff" strokeOpacity=".5" />
  </svg>;
}

export function WorkspaceEmpty({ workspaceId, title, description, art }: {
  workspaceId: string;
  title: string;
  description: string;
  art: ReactNode;
}) {
  return <div className="empty-state">
    <section className="empty-state-card">
      <div className="empty-state-art">{art}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      <div className="empty-state-actions">
        <Link className="empty-state-primary" to="/search">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>
          去论文搜索
        </Link>
        <Link className="empty-state-secondary" to={`/w/${workspaceId}/research`}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 4.2h9.6v6.1H6.4L3.2 12.6V4.2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          去检索助手
        </Link>
      </div>
    </section>
  </div>;
}

export function CompareEmpty({ workspaceId }: { workspaceId: string }) {
  return <WorkspaceEmpty workspaceId={workspaceId} title="还没有可对比的论文" description="将论文加入当前工作区后，可选择 2–5 篇进行对比。" art={<CompareArt />} />;
}

export function GraphEmpty({ workspaceId }: { workspaceId: string }) {
  return <WorkspaceEmpty workspaceId={workspaceId} title="还没有可展示的知识图谱" description="向当前工作区添加论文后，将基于论文、作者和关键词生成图谱。" art={<GraphArt />} />;
}
