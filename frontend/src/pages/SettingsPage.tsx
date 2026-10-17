import { useEffect, useState } from "react";
import { Button, Input, Switch } from "antd";
import { ApiOutlined, PlusOutlined } from "@ant-design/icons";
import { providers, useProviders, type ProviderConn, type ProviderModel } from "../providers";

const SECTIONS = [{ key: "providers", label: "模型提供商" }, { key: "about", label: "说明" }] as const;

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
        <p className="page-sub">模型连接和密钥只保存在当前浏览器，还不会发给后端，也不会进入检索或分析。换浏览器或清除站点数据后需要重新填写。</p>
      </section> : <>
        <h1 className="page-title">模型提供商</h1>
        <p className="page-sub">为工作区里的提问和比较准备模型连接。可以先只配一个可用模型。</p>
        <section className="provider-card" aria-label="已连接的提供商">
          <h2>已连接的提供商</h2>
          {!list.length && !creating ? <p className="empty-hint">还没有提供商。添加一个连接后，才能在提问时选择经济模型或强能力模型。</p> : null}
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
