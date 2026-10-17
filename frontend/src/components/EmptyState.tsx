import type { ReactNode } from "react";
import { Link } from "react-router-dom";

function Icon({ children }: { children: ReactNode }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>;
}

function CompareArt() {
  return <svg className="empty-art" viewBox="0 0 220 128" aria-hidden="true">
    <rect x="36" y="30" width="70" height="82" rx="12" fill="#e8f1ff" stroke="#c9daf8" />
    <rect x="50" y="46" width="26" height="5" rx="2.5" fill="#7ea6f5" />
    <rect x="50" y="58" width="42" height="4" rx="2" fill="#d4e3fb" />
    <rect x="50" y="68" width="34" height="4" rx="2" fill="#d4e3fb" />
    <rect x="50" y="78" width="38" height="4" rx="2" fill="#d4e3fb" />
    <rect x="114" y="16" width="70" height="82" rx="12" fill="#f3eeff" stroke="#ddd0f8" />
    <rect x="128" y="32" width="26" height="5" rx="2.5" fill="#b59af0" />
    <rect x="128" y="44" width="42" height="4" rx="2" fill="#e4daf8" />
    <rect x="128" y="54" width="34" height="4" rx="2" fill="#e4daf8" />
    <rect x="128" y="64" width="38" height="4" rx="2" fill="#e4daf8" />
    <circle cx="110" cy="64" r="16" fill="#fff" stroke="#e5e9f2" />
    <text x="110" y="68" textAnchor="middle" fill="#8b93a3" fontSize="11" fontWeight="700" fontFamily="Segoe UI, sans-serif">vs</text>
  </svg>;
}

function GraphArt() {
  return <svg className="empty-art" viewBox="0 0 240 140" aria-hidden="true">
    <path d="M72 48 104 70M168 48 136 70M64 100 100 84M176 100 140 84" fill="none" stroke="#d3e1f8" strokeWidth="1.6" />
    <circle cx="58" cy="40" r="18" fill="#eef4ff" stroke="#d3e2fb" />
    <circle cx="182" cy="40" r="18" fill="#eef4ff" stroke="#d3e2fb" />
    <circle cx="52" cy="106" r="18" fill="#eef4ff" stroke="#d3e2fb" />
    <circle cx="188" cy="106" r="18" fill="#f4f0ff" stroke="#e0d7f8" />
    <circle cx="58" cy="36" r="3.4" fill="#6f97e8" />
    <path d="M50.8 48.2c1.2-3.6 3.6-5.4 7.2-5.4s6 1.8 7.2 5.4" fill="none" stroke="#6f97e8" strokeWidth="1.5" strokeLinecap="round" />
    <path d="M174 34.5h11.5l-2 4.4h-7.4L174 34.5Z" fill="none" stroke="#6f97e8" strokeWidth="1.4" strokeLinejoin="round" />
    <path d="M176.2 38.9h8.6v6.4h-8.6z" fill="none" stroke="#6f97e8" strokeWidth="1.4" />
    <rect x="45" y="100" width="14" height="11" rx="2" fill="none" stroke="#6f97e8" strokeWidth="1.4" />
    <path d="M48 103.5h8M48 106.5h5.5" stroke="#6f97e8" strokeWidth="1.2" strokeLinecap="round" />
    <circle cx="188" cy="100" r="2.4" fill="none" stroke="#8d74d8" strokeWidth="1.4" />
    <circle cx="181" cy="111" r="2.4" fill="none" stroke="#8d74d8" strokeWidth="1.4" />
    <circle cx="195" cy="111" r="2.4" fill="none" stroke="#8d74d8" strokeWidth="1.4" />
    <path d="m186 101.6-3.2 7M190 101.6l3.2 7M183.2 111h9.6" stroke="#8d74d8" strokeWidth="1.2" />
    <rect x="92" y="54" width="56" height="44" rx="12" fill="#fff" stroke="#d5e2fb" />
    <rect x="104" y="68" width="22" height="4" rx="2" fill="#8eb0f6" />
    <rect x="104" y="78" width="32" height="3" rx="1.5" fill="#d5e3fb" />
    <rect x="104" y="86" width="24" height="3" rx="1.5" fill="#d5e3fb" />
  </svg>;
}

export function WorkspaceEmpty({ workspaceId, title, description, art, features }: {
  workspaceId: string;
  title: string;
  description: string;
  art: ReactNode;
  features: { title: string; text: string; icon: ReactNode }[];
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
        <Link className="empty-state-secondary" to={`/w/${workspaceId}`}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 4.2h9.6v6.1H6.4L3.2 12.6V4.2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          去 AI 研究助手
        </Link>
      </div>
      <ul className="empty-state-features">
        {features.map(item => <li key={item.title}><i>{item.icon}</i><strong>{item.title}</strong><span>{item.text}</span></li>)}
      </ul>
    </section>
  </div>;
}

export function CompareEmpty({ workspaceId }: { workspaceId: string }) {
  return <WorkspaceEmpty
    workspaceId={workspaceId}
    title="还没有可对比的论文"
    description="先将论文加入当前工作区，再选择 2–5 篇进行对比，快速了解它们的异同。"
    art={<CompareArt />}
    features={[
      { title: "研究问题", text: "对比不同论文的研究目标与问题定义", icon: <Icon><path d="M7 3.8h6.4L18 8.2V20H7z" /><path d="M13.2 3.8V8.2H18" /><path d="M10 12.2h5M10 15.5h3.2" /></Icon> },
      { title: "方法与模型", text: "梳理方法、模型结构与技术路线", icon: <Icon><path d="M12 4 20 8 12 12 4 8Z" /><path d="M4 12l8 4 8-4M4 16l8 4 8-4" /></Icon> },
      { title: "实验结果", text: "分析实验设置与结果表现", icon: <Icon><path d="M5 19V5M5 19h14" /><path d="M9 15.5v-4M13 15.5V8.5M17 15.5v-2" /></Icon> },
    ]}
  />;
}

export function GraphEmpty({ workspaceId }: { workspaceId: string }) {
  return <WorkspaceEmpty
    workspaceId={workspaceId}
    title="还没有可展示的知识图谱"
    description="向当前工作区添加论文后，系统会基于论文、作者、关键词等信息生成知识图谱，帮助你发现研究领域的关联。"
    art={<GraphArt />}
    features={[
      { title: "论文关系", text: "展示论文与主题之间的关联", icon: <Icon><circle cx="7" cy="15" r="2.1" /><circle cx="16" cy="7" r="2.1" /><circle cx="17.5" cy="16.5" r="2.1" /><path d="M8.8 13.4 14.2 8.6M8.8 15.6l6.6 1" /></Icon> },
      { title: "作者与关键词", text: "发现反复出现的作者和主题", icon: <Icon><circle cx="12" cy="8" r="2.4" /><path d="M6.8 18.2c.8-2.8 2.7-4.2 5.2-4.2s4.4 1.4 5.2 4.2" /></Icon> },
      { title: "图谱可视化", text: "用节点和连线浏览研究脉络", icon: <Icon><circle cx="6" cy="12" r="2" /><circle cx="16" cy="7" r="2" /><circle cx="17" cy="17" r="2" /><path d="M8 11.2 14 8.2M8 12.8 15 16" /></Icon> },
    ]}
  />;
}
