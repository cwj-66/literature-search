import { pageTitle } from "../../presentation";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Dropdown, Empty, Input, Modal, Segmented, message } from "antd";
import { CopyOutlined, DeleteOutlined, EllipsisOutlined, ExportOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, PushpinFilled, SearchOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { RenameDialog } from "../../components/RenameDialog";
import { exportChat, useResearchChats, type ResearchChat } from "../../researchChats";
import { ResearchConversation } from "./ResearchConversation";
import { useCollapsed } from "../../hooks";

export function ResearchChatSessions({ id, name }: { id: string; name: string }) {
  const store = useResearchChats(id, name);
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [messageApi, context] = message.useMessage();
  const [collapsed, toggleCollapsed] = useCollapsed("sg-research-chats");
  const seeded = useRef(false);
  const visible = store.chats.filter(chat => !!chat.archived === archived &&
    `${chat.title}\n${chat.turns.map(turn => `${turn.question}\n${turn.task?.answer ?? ""}`).join("\n")}`.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
  const active = store.chats.find(chat => chat.id === params.get("conversation")) ?? visible[0];
  const renaming = store.chats.find(chat => chat.id === renameId);
  const create = (copy?: ResearchChat, sources?: string[]) => {
    const chat: ResearchChat = { id: crypto.randomUUID(), title: copy ? `${copy.title.slice(0, 110)}（副本）` : "新对话", titleEdited: !!copy,
      createdAt: Date.now(), updatedAt: Date.now(), selectedIds: sources ?? [...(copy?.selectedIds ?? [])],
      turns: copy ? copy.turns.map(turn => turn.pending || turn.task?.status === "waiting"
        ? { id: turn.id, question: turn.question, request: turn.request, note: "原对话的未完成任务未复制，可重试原问题。" }
        : structuredClone(turn)) : [] };
    store.update(chat); setArchived(false); setQuery(""); setParams({ conversation: chat.id });
  };
  useEffect(() => {
    if (store.ready && params.get("papers") && !seeded.current) { seeded.current = true; create(undefined, params.get("papers")!.split(",").filter(Boolean)); }
  }, [store.ready]);
  useEffect(() => { document.title = pageTitle(`${active?.title ?? "检索助手"} - ${name}`); }, [active?.title, name]);
  function patch(chat: ResearchChat, changes: Partial<ResearchChat>) { store.patch(chat.id, changes); }
  function remove(chat: ResearchChat) {
    Modal.confirm({ title: `删除对话「${chat.title}」？`, content: "删除聊天记录和本对话的资料选择。工作区中的论文会保留。此操作无法撤销。",
      okText: "删除对话", cancelText: "取消", okButtonProps: { danger: true },
      onOk: async () => { try { await store.remove(chat); if (active?.id === chat.id) setParams({}); }
        catch (err) { messageApi.error(err instanceof Error ? err.message : "删除失败"); throw err; } } });
  }
  function action(key: string, chat: ResearchChat) {
    if (key === "rename") setRenameId(chat.id);
    if (key === "pin") patch(chat, { pinned: !chat.pinned });
    if (key === "export") exportChat(chat, name);
    if (key === "copy") create(chat);
    if (key === "archive") { patch(chat, { archived: !chat.archived }); if (active?.id === chat.id) setParams({}); }
    if (key === "delete") remove(chat);
  }
  return <div className="research-layout">
    {context}
    <aside className={`research-chats${collapsed ? " is-collapsed" : ""}`} aria-label="检索对话列表">
      <div className="rail-head">{collapsed ? null : <h2>检索助手</h2>}<button type="button" className="side-collapse" aria-expanded={!collapsed} aria-label={collapsed ? "展开对话列表" : "收起对话列表"} title={collapsed ? "展开对话列表" : "收起对话列表"} onClick={toggleCollapsed}>{collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button></div>
      <Button type="primary" aria-label="新建对话" block={!collapsed} icon={collapsed ? <PlusOutlined /> : undefined} disabled={!store.ready || busy} onClick={() => create()}>{collapsed ? null : "新建对话"}</Button>
      {collapsed ? null : <><Input allowClear prefix={<SearchOutlined />} aria-label="搜索研究对话" placeholder="搜索名称或聊天内容" value={query} onChange={e => setQuery(e.target.value)} />
      <Segmented className="research-chat-filter" disabled={busy} block value={archived ? "archived" : "active"} onChange={value => { setArchived(value === "archived"); setParams({}); }}
        options={[{ label: "对话", value: "active" }, { label: `归档 ${store.chats.filter(c => c.archived).length}`, value: "archived" }]} />
      <p className="research-sync-state" role="status">{!store.ready ? "正在读取已保存对话…" : store.error ? "同步失败，浏览器副本已保留" : store.pending ? "正在保存到服务器…" : "已保存到服务器"}</p>
      <div className="research-chat-list">{visible.map(chat => <div className={`research-chat-row${active?.id === chat.id ? " is-active" : ""}`} key={chat.id}>
        <button type="button" className="research-chat-open" disabled={busy} aria-current={active?.id === chat.id ? "true" : undefined} title={chat.title} onClick={() => setParams({ conversation: chat.id })}>
          {chat.pinned ? <PushpinFilled className="research-pin" aria-label="已置顶" /> : null}{chat.title}
        </button>
        <Dropdown trigger={["click"]} menu={{ onClick: item => action(item.key, chat), items: [
          { key: "rename", label: "重命名", disabled: !store.ready },
          { key: "pin", label: chat.pinned ? "取消置顶" : "置顶", disabled: !store.ready },
          { key: "copy", label: "复制为新对话", icon: <CopyOutlined />, disabled: busy || !store.ready || chat.turns.some(t => t.pending) },
          { key: "export", label: "导出 Markdown", icon: <ExportOutlined /> },
          { type: "divider" },
          { key: "archive", label: chat.archived ? "恢复对话" : "归档", disabled: busy || !store.ready || chat.turns.some(t => t.pending) },
          { key: "delete", label: "删除对话", icon: <DeleteOutlined />, danger: true, disabled: busy || !store.ready || chat.turns.some(t => t.pending) },
        ] }}>
          <button type="button" className="research-chat-more" aria-label={`对话操作：${chat.title}`} title="对话操作"><EllipsisOutlined /></button>
        </Dropdown>
      </div>)}{!visible.length && store.ready ? <p className="small-print">{query ? "没有匹配的对话" : archived ? "还没有归档对话" : "新建一个对话开始研究"}</p> : null}</div>
      <Link to={`/w/${id}/studio`}>打开智能问答</Link></>}
    </aside>
    <section className="research-conversation-main">
      {store.error ? <Alert type="warning" showIcon message={store.error} action={<Button size="small" onClick={store.retry}>重试同步</Button>} /> : null}
      {active?.archived ? <Alert type="info" message="这是已归档的对话，恢复后可继续研究。" action={<Button size="small" disabled={!store.ready} onClick={() => { patch(active, { archived: false }); setArchived(false); }}>恢复对话</Button>} /> : null}
      {active ? <>
        <header className="research-conversation-head"><div><h2>{active.title}</h2>{active.pinned ? <span><PushpinFilled /> 已置顶</span> : null}</div>
          <Button size="small" icon={<ExportOutlined />} onClick={() => exportChat(active, name)}>导出</Button></header>
        <ResearchConversation key={`${active.id}:${store.ready}`} id={id} chat={active} onBusy={setBusy} disabled={!store.ready || !!active.archived}
          onChange={changes => patch(active, changes)} />
      </> : <div className="research-empty"><Empty description={store.ready ? "开始一个研究对话，找论文、读论文或比较研究方法。" : "正在加载研究对话…"}>
        <Button type="primary" disabled={!store.ready} onClick={() => create()}>新建对话</Button></Empty></div>}
    </section>
    <RenameDialog open={!!renaming} title="重命名对话" label="对话名称" value={renaming?.title ?? ""} maxLength={120}
      onClose={() => setRenameId(null)} onSave={title => { if (renaming) patch(renaming, { title, titleEdited: true }); }} />
  </div>;
}
