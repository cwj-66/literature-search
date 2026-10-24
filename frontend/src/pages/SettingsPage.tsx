import { pageTitle } from "../presentation";
import { useEffect, useState } from "react";
import { Alert, Button, Input } from "antd";
import { fetchSources, saveSemanticKey } from "../api";
import { isDemo, demoInfo } from "../demo";

const SECTIONS = [{ key: "providers", label: "模型配置" }, { key: "sources", label: "检索数据源" }, { key: "about", label: "说明" }] as const;

function SemanticKeyCard() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetchSources(controller.signal)
      .then(data => setConfigured(data.semantic_scholar.key_configured))
      .catch(() => { if (!controller.signal.aborted) setConfigured(null); });
    return () => controller.abort();
  }, []);
  async function save(key: string) {
    setSaving(true); setError(""); setNote("");
    try {
      const result = await saveSemanticKey(key);
      setConfigured(result.key_configured);
      setDraft("");
      setNote(result.key_configured ? "密钥已保存到服务器，检索会立即使用。" : "已清除服务器上的密钥，检索回到公开匿名额度。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }
  return <section className="provider-card" aria-label="Semantic Scholar 密钥">
    <h2>Semantic Scholar API Key</h2>
    <p className="field-help">
      状态：{configured === null ? "正在检查…" : configured ? "已配置" : "未配置（使用公开匿名额度，容易限流）"}。
      密钥只保存在服务器，用于访问 Semantic Scholar 官方 API，不会回显。
      没有密钥可到 <a href="https://www.semanticscholar.org/product/api#api-key-form" target="_blank" rel="noreferrer">Semantic Scholar 申请</a>。
    </p>
    <div className="provider-edit-actions">
      <Input.Password value={draft} maxLength={200} placeholder={configured ? "输入新密钥以替换" : "粘贴 API Key"}
        aria-label="Semantic Scholar API Key" onChange={e => { setDraft(e.target.value); setError(""); setNote(""); }} />
      <Button type="primary" loading={saving} disabled={!draft.trim()} onClick={() => void save(draft)}>保存</Button>
      {configured ? <Button danger loading={saving} onClick={() => void save("")}>清除密钥</Button> : null}
    </div>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {note ? <Alert type="success" showIcon message={note} /> : null}
  </section>;
}

type Runtime = { mode: string; model: string; llm_configured: boolean; embedding_configured: boolean; parser_configured: boolean };

function ServerSettings() {
  const [section, setSection] = useState<(typeof SECTIONS)[number]["key"]>("providers");
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/runtime", { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("无法读取服务配置状态，请稍后刷新。");
      setRuntime(await response.json());
    }).catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, []);
  const preview = runtime?.mode === "public_preview";
  return <div className="settings-page">
    <nav className="settings-nav" aria-label="设置分区">{SECTIONS.map(item => <button type="button" key={item.key} aria-current={section === item.key ? "page" : undefined} onClick={() => setSection(item.key)}>{item.label}</button>)}</nav>
    <main className="settings-main">
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {preview ? <Alert type="info" showIcon message="开源版公开预览" description="此站不提供作者的模型密钥，也不加载作品集演示数据。启用 AI 能力请克隆项目、自行部署并配置服务。" /> : null}
      {section === "sources" ? preview ? <section className="provider-card"><h1 className="page-title">检索数据源</h1><p>arXiv 与 Semantic Scholar 可使用公开接口，可能受到上游限流。</p><p>公开预览不接收或保存个人 API Key。自行部署后，可在服务端配置可选的 Semantic Scholar 密钥。</p></section> : runtime ? <SemanticKeyCard /> : null
      : section === "about" ? <section className="provider-card"><h1 className="page-title">部署说明</h1><p>普通部署从空工作区开始，模型、向量服务与全文解析均使用部署者自己的服务配置。</p><p>本地配置文件为 backend/.env，Docker Compose 默认配置文件为 deploy/.env。修改环境变量后需重新启动后端；仅在浏览器保存连接信息不会改变服务端调用。</p><p>单人自部署版不包含多用户权限系统。公开部署前应另行配置认证和用户数据隔离。</p><a href="https://github.com/cwj-66/literature-search#快速开始" target="_blank" rel="noreferrer">查看项目 README →</a></section>
      : <section className="provider-card"><h1 className="page-title">模型配置</h1>
        {!runtime && !error ? <p>正在读取服务配置…</p> : null}
        {runtime ? <><p>大模型：{runtime.llm_configured ? `已配置（${runtime.model}）` : "未配置"}</p><p>向量与重排：{runtime.embedding_configured ? "已配置" : "未配置"}</p><p>MinerU 全文解析：{runtime.parser_configured ? "已配置" : "未配置"}</p></> : null}
        <p>密钥由部署者在服务端配置，不会返回浏览器。论文搜索可使用公开接口；研究问答、向量检索和全文解析需要相应服务。</p>
        <a href="https://github.com/cwj-66/literature-search#配置服务" target="_blank" rel="noreferrer">查看配置方法 →</a>
      </section>}
    </main>
  </div>;
}

export default function SettingsPage() {
  useEffect(() => { document.title = pageTitle("设置"); }, []);
  if (isDemo) return <main className="settings-main">
    <h1 className="page-title">演示配置</h1>
    <section className="provider-card"><h2>演示模型</h2><p>{demoInfo?.model_configured ? "已配置，可直接在检索助手中提问。" : "模型暂不可用，请联系作者。"}</p><p>当前模型：{demoInfo?.model}</p><p>模型和检索服务由作者配置，无需填写 API Key。</p></section>
    <section className="provider-card"><h2>体验数据</h2><p>每位访客有独立的示例工作区。新建工作区、增删论文与对话不会改变其他访客的体验。</p><p>每天最多发起 30 次研究或处理请求。</p></section>
  </main>;
  return <ServerSettings />;
}
