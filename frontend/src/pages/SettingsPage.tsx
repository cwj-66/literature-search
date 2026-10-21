import { useEffect, useState } from "react";
import { Alert, Button, Input, Switch } from "antd";
import { ApiOutlined, PlusOutlined } from "@ant-design/icons";
import { fetchSources, saveSemanticKey } from "../api";
import { providers, useProviders, type ProviderConn, type ProviderModel } from "../providers";

const SECTIONS = [{ key: "providers", label: "模型提供商" }, { key: "sources", label: "检索数据源" }, { key: "about", label: "说明" }] as const;

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

function blank(): Omit<ProviderConn, "id"> {
  return { name: "", baseUrl: "https://api.openai.com/v1", apiKey: "", enabled: true, models: [{ id: "", economy: true, strong: false }] };
}

function Editor({ initial, onSave, onCancel }: { initial: Omit<ProviderConn, "id">; onSave: (value: Omit<ProviderConn, "id">) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(initial);
  const models = draft.models.length ? draft.models : [{ id: "", economy: true, strong: false }];
  const setModel = (index: number, patch: Partial<ProviderModel>) => setDraft(value => ({ ...value, models: models.map((item, i) => i === index ? { ...item, ...patch } : item) }));
  return <div className="provider-edit">
    <label className="field-label">名称</label>
    <Input value={draft.name} maxLength={40} placeholder="例如：硅基流动" onChange={e => setDraft(v => ({ ...v, name: e.target.value }))} />
    <label className="field-label">BASE URL</label>
    <Input value={draft.baseUrl} maxLength={200} onChange={e => setDraft(v => ({ ...v, baseUrl: e.target.value }))} />
    <label className="field-label">API KEY</label>
    <Input.Password value={draft.apiKey} maxLength={200} placeholder="只保存在这台浏览器" onChange={e => setDraft(v => ({ ...v, apiKey: e.target.value }))} />
    <p className="field-label">模型</p>
    {models.map((model, index) => <div className="provider-model" key={index}>
      <Input value={model.id} maxLength={80} placeholder="模型标识，例如 qwen-plus" onChange={e => setModel(index, { id: e.target.value })} />
      <label><Switch size="small" checked={model.economy} onChange={economy => setModel(index, { economy })} /> 经济</label>
      <label><Switch size="small" checked={model.strong} onChange={strong => setModel(index, { strong })} /> 强能力</label>
      <Button type="text" disabled={models.length <= 1} onClick={() => setDraft(v => ({ ...v, models: models.filter((_, i) => i !== index) }))}>移除</Button>
    </div>)}
    <Button type="text" icon={<PlusOutlined />} onClick={() => setDraft(v => ({ ...v, models: [...models, { id: "", economy: false, strong: false }] }))}>添加模型</Button>
    <div className="provider-edit-actions">
      <Button onClick={onCancel}>取消</Button>
      <Button type="primary" disabled={!draft.name.trim() || !draft.baseUrl.trim()} onClick={() => onSave({ ...draft, name: draft.name.trim(), models: models.filter(model => model.id.trim()).map(model => ({ ...model, id: model.id.trim() })) })}>保存</Button>
    </div>
  </div>;
}

export default function SettingsPage() {
  const list = useProviders();
  const [section, setSection] = useState<(typeof SECTIONS)[number]["key"]>("providers");
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => { document.title = "设置 - 文献研究助手"; }, []);
  return <div className="settings-page">
    <nav className="settings-nav" aria-label="设置分区">
      {SECTIONS.map(item => <button type="button" key={item.key} aria-current={section === item.key ? "page" : undefined} onClick={() => setSection(item.key)}>{item.label}</button>)}
    </nav>
    <main className="settings-main">
      {section === "about" ? <section>
        <h1 className="page-title">说明</h1>
        <p className="page-sub">模型连接和密钥只保存在当前浏览器，不会发送到后端，也不参与检索与分析。更换浏览器或清除站点数据后需要重新填写。</p>
      </section> : section === "sources" ? <>
        <h1 className="page-title">检索数据源</h1>
        <SemanticKeyCard />
      </> : <>
        <h1 className="page-title">模型提供商</h1>
        <section className="provider-card" aria-label="已连接的提供商">
          <h2>已连接的提供商</h2>
          {!list.length && !creating ? <p className="empty-hint">还没有提供商，添加连接后才能在提问时选择模型。</p> : null}
          {list.map(item => {
            const shown = open === item.id;
            return <article key={item.id} className="provider-row">
              <div className="provider-row-head">
                <ApiOutlined aria-hidden />
                <strong>{item.name}</strong>
                <button type="button" className="link-button" onClick={() => setOpen(shown ? null : item.id)}>{shown ? "收起" : "显示"}</button>
                <button type="button" className={`provider-enabled${item.enabled ? " is-on" : ""}`} onClick={() => providers.update(item.id, { enabled: !item.enabled })}>{item.enabled ? "已启用" : "已停用"}</button>
              </div>
              {shown ? <>
                <Editor key={item.id} initial={item} onCancel={() => setOpen(null)} onSave={value => { providers.update(item.id, value); setOpen(null); }} />
                <Button danger type="text" onClick={() => providers.remove(item.id)}>断开连接</Button>
              </> : null}
            </article>;
          })}
          {creating ? <Editor initial={blank()} onCancel={() => setCreating(false)} onSave={value => { providers.add(value); setCreating(false); }} /> : <Button icon={<PlusOutlined />} onClick={() => { setCreating(true); setOpen(null); }}>添加提供商</Button>}
        </section>
      </>}
    </main>
  </div>;
}
