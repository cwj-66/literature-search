import { useEffect, useState } from "react";
import { Alert, Button, Input, Select } from "antd";
import { pageTitle } from "../presentation";

type Settings = Record<string, string | boolean>;
const GROUPS = [
  { title: "大模型", fields: [["llm_base", "API 地址"], ["llm_model", "模型名称"], ["llm_key", "API Key"]] },
  { title: "向量与重排", fields: [["embedding_base", "API 地址"], ["embedding_model", "向量模型"], ["rerank_model", "重排模型"], ["embedding_key", "API Key"]] },
  { title: "全文解析", fields: [["mineru_key", "MinerU API Token"]] },
  { title: "检索数据源", fields: [["semantic_key", "Semantic Scholar API Key（可选）"]] },
];
const PRESETS = [
  { label: "阿里云百炼", value: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { label: "DeepSeek", value: "https://api.deepseek.com/v1" },
  { label: "硅基流动", value: "https://api.siliconflow.cn/v1" },
  { label: "OpenAI", value: "https://api.openai.com/v1" },
  { label: "Moonshot", value: "https://api.moonshot.cn/v1" },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    document.title = pageTitle("设置");
    const controller = new AbortController();
    fetch("/api/providers", { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error("读取配置失败，请刷新重试");
      const data = await r.json() as Settings;
      setSettings(data);
      setDraft(Object.fromEntries(Object.entries(data).filter(([, value]) => typeof value === "string")) as Record<string, string>);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  async function save(values: Record<string, string>) {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await fetch("/api/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "保存失败，请重试");
      setSettings(data);
      setDraft(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => !key.endsWith("_key"))));
      setNote("配置已保存，后续请求将使用此配置。");
    } catch (e) { setError(e instanceof Error ? e.message : "保存失败"); }
    finally { setBusy(false); }
  }
  function submit() {
    if (!draft.llm_model?.trim() && (draft.llm_key?.trim() || settings?.llm_key_configured)) {
      setError("请填写要使用的模型名称"); return;
    }
    void save(Object.fromEntries(Object.entries(draft).filter(([key, value]) => !key.endsWith("_key") || value.trim())));
  }
  return <main className="settings-main provider-settings">
    <h1 className="page-title">模型与 API 配置</h1>
    <p className="page-sub">填写自己的服务地址和 API Key，即可开始研究。密钥保存后不回显；后续请求会使用已保存的配置。</p>
    {settings?.session_scoped ? <p className="field-help">配置与工作区绑定当前浏览器会话，不与其他访问者共享。清除站点 Cookie 后需重新配置。</p> : null}
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {note ? <Alert type="success" showIcon message={note} /> : null}
    {GROUPS.map(group => <section className="provider-card" key={group.title}>
      <h2>{group.title}</h2>
      {group.title === "大模型" ? <><label className="field-label" htmlFor="llm-preset">服务提供商</label><Select id="llm-preset" aria-label="服务提供商" options={PRESETS} value={PRESETS.some(p => p.value === draft.llm_base) ? draft.llm_base : undefined} placeholder="选择服务商，或填写下方地址" onChange={value => setDraft(p => ({ ...p, llm_base: value }))} /></> : null}
      {group.fields.map(([key, label]) => <div className="provider-setting-field" key={key}>
        <label className="field-label" htmlFor={key}>{label}</label>
        {key.endsWith("_key") ? <div className="provider-edit-actions"><Input.Password id={key} aria-label={`${group.title} ${label}`} autoComplete="off" value={draft[key] || ""} placeholder={settings?.[key + "_configured"] ? "已配置；留空保持原值" : "粘贴你的 API Key"} onChange={e => setDraft(p => ({ ...p, [key]: e.target.value }))} />
          {settings?.[key + "_configured"] ? <Button disabled={busy} onClick={() => void save({ [key]: "" })}>清除</Button> : null}
        </div> : <Input id={key} aria-label={`${group.title} ${label}`} value={draft[key] || ""} placeholder={key === "llm_model" ? "例如 qwen-plus、deepseek-chat" : "填写配置"} onChange={e => setDraft(p => ({ ...p, [key]: e.target.value }))} />}
      </div>)}
      {group.title === "向量与重排" ? <p className="field-help">默认使用 BGE-M3 与 BGE reranker。更换向量模型需由部署者匹配 tokenizer 和输入上限，再重建索引。</p> : null}
      {group.title === "检索数据源" ? <p className="field-help">留空可使用公开检索接口；上游服务可能限流。</p> : null}
    </section>)}
    <Button type="primary" loading={busy} disabled={!settings} onClick={submit}>保存配置</Button>
  </main>;
}
