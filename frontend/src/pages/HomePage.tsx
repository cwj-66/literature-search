import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Dropdown, Input, Modal, Select } from "antd";
import { ClockCircleOutlined, EllipsisOutlined, FileTextOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { deleteWorkspace } from "../api";
import { WorkspaceRenameDialog } from "../components/WorkspaceRenameDialog";
import { COVERS, CoverArt, WorkspaceCover, readCoverFile } from "../covers";
import { DEFAULT_COVER, projectOpened, projectUpdated, projects, useProjects, type CoverId, type Project } from "../projects";

type SortKey = "opened" | "updated" | "created";
type FilterKey = "all" | "filled" | "empty";
type CoverChoice = { kind: "preset"; id: CoverId } | { kind: "image"; url: string };

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(diff) || diff < 60_000) return "刚刚";
  const minute = Math.floor(diff / 60_000);
  if (minute < 60) return `${minute} 分钟前`;
  const hour = Math.floor(minute / 60);
  if (hour < 24) return `${hour} 小时前`;
  const day = Math.floor(hour / 24);
  if (day < 7) return `${day} 天前`;
  const week = Math.floor(day / 7);
  if (week < 5) return `${week} 周前`;
  const month = Math.floor(day / 30);
  if (month < 12) return `${month} 个月前`;
  return `${Math.floor(day / 365)} 年前`;
}

function CreateWorkspace({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Modal
    className="ws-create"
    open={open}
    width={640}
    centered
    destroyOnHidden
    title={<div className="ws-modal-title"><h2>创建研究工作区</h2></div>}
    footer={null}
    onCancel={onClose}
    styles={{ content: { borderRadius: 18, padding: "22px 24px 20px" }, header: { marginBottom: 18 }, body: { padding: 0 } }}
  >
    {open ? <CreateWorkspaceForm onClose={onClose} /> : null}
  </Modal>;
}

function CreateWorkspaceForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [cover, setCover] = useState<CoverChoice>({ kind: "preset", id: DEFAULT_COVER });
  const [uploadError, setUploadError] = useState("");

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/")) { setUploadError("请选择图片文件"); return; }
    if (file.size > 8 * 1024 * 1024) { setUploadError("图片请小于 8MB"); return; }
    try {
      setCover({ kind: "image", url: await readCoverFile(file) });
      setUploadError("");
    } catch {
      setUploadError("这张图片无法读取，请换一张");
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const title = name.trim();
    if (!title) return;
    const id = projects.create(title, description, cover.kind === "image" ? { coverImage: cover.url } : { cover: cover.id });
    onClose();
    navigate(`/w/${id}`);
  };

  return <>
    <form id="ws-create-form" onSubmit={submit}>
      <label className="ws-field" htmlFor="ws-name"><span>工作区名称</span>
        <Input id="ws-name" autoFocus value={name} maxLength={40} placeholder="例如：机器学习" onChange={event => setName(event.target.value)} />
      </label>
      <label className="ws-field" htmlFor="ws-desc"><span>研究描述（选填）</span>
        <Input.TextArea id="ws-desc" value={description} maxLength={120} autoSize={{ minRows: 3, maxRows: 5 }} placeholder="例如：研究模型泛化能力、训练方法与实验结果。" onChange={event => setDescription(event.target.value)} />
      </label>
      <section className="ws-section">
        <h3>选择封面（选填）</h3>
        <div className="ws-covers" role="radiogroup" aria-label="工作区封面">
          {COVERS.map(item => {
            const on = cover.kind === "preset" && cover.id === item.id;
            return <button type="button" key={item.id} className={on ? "ws-cover-pick is-on" : "ws-cover-pick"} role="radio" aria-checked={on} aria-label={item.label} onClick={() => setCover({ kind: "preset", id: item.id })}>
              <CoverArt id={item.id} />
            </button>;
          })}
        </div>
        <div className="ws-upload-line">
          {cover.kind === "image" ? <img className="ws-upload-preview" src={cover.url} alt="" /> : null}
          <button type="button" className="ws-upload-btn" onClick={() => fileRef.current?.click()}>{cover.kind === "image" ? "更换图片" : "上传封面"}</button>
          <span className="ws-upload-hint">{cover.kind === "image" ? "将使用这张图片作为封面" : "不上传则使用当前选中的预设封面"}</span>
        </div>
        {uploadError ? <p className="inline-error">{uploadError}</p> : null}
        <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={onFile} />
      </section>
    </form>
    <div className="ws-modal-foot">
      <button type="button" className="ws-btn" onClick={onClose}>取消</button>
      <button type="submit" form="ws-create-form" className="ws-btn ws-btn-primary" disabled={!name.trim()}>创建工作区</button>
    </div>
  </>;
}

function CardMenu({ item, onUpload, onDelete, onCover, onRename }: { item: Project; onUpload: () => void; onDelete: () => void; onCover: (id: CoverId) => void; onRename: () => void }) {
  const custom = Boolean(item.coverImage);
  return <div className="ws-card-menu">
    <button type="button" className="ws-menu-action" onClick={onRename}>重命名工作区</button>
    <div className="ws-menu-split" />
    <p>更换封面</p>
    <div className="ws-mini-covers">
      {COVERS.map(cover => {
        const on = !custom && (item.cover ?? DEFAULT_COVER) === cover.id;
        return <button type="button" key={cover.id} className={on ? "is-on" : undefined} aria-label={cover.label} aria-pressed={on} onClick={() => onCover(cover.id)}>
          <CoverArt id={cover.id} />
        </button>;
      })}
    </div>
    <button type="button" className="ws-menu-action" onClick={onUpload}>上传封面</button>
    <div className="ws-menu-split" />
    <button type="button" className="ws-menu-action is-danger" onClick={onDelete}>删除工作区</button>
  </div>;
}

export default function HomePage() {
  const { projects: list } = useProjects();
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadFor = useRef("");
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("opened");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [renameId, setRenameId] = useState<string | null>(null);
  useEffect(() => { document.title = "研究工作区 - 文献研究助手"; }, []);

  const shown = [...list].sort((a, b) => {
    if (sort === "created") return b.createdAt.localeCompare(a.createdAt);
    if (sort === "opened") return projectOpened(b).localeCompare(projectOpened(a));
    return projectUpdated(b).localeCompare(projectUpdated(a));
  }).filter(item => {
    const blob = `${item.name} ${item.description}`.toLowerCase();
    if (query.trim() && !blob.includes(query.trim().toLowerCase())) return false;
    const count = Object.keys(item.papers).length;
    if (filter === "filled") return count > 0;
    if (filter === "empty") return count === 0;
    return true;
  });
  const filteredEmpty = shown.length === 0 && (list.length > 0 || Boolean(query.trim()) || filter !== "all");

  const onCoverFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const id = uploadFor.current;
    event.target.value = "";
    if (!file || !id) return;
    if (!file.type.startsWith("image/") || file.size > 8 * 1024 * 1024) { setNotice("请选择 8MB 以内的图片"); return; }
    try {
      projects.setCoverImage(id, await readCoverFile(file));
      setNotice("");
    } catch {
      setNotice("这张图片无法读取，请换一张");
    }
  };

  const remove = (item: Project) => {
    setMenuFor(null);
    Modal.confirm({
      title: `删除工作区「${item.name}」？`,
      content: "删除工作区内的对话、阅读记录和笔记，文献库不受影响。",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => { await deleteWorkspace(item.id); projects.select(item.id); projects.removeProject(); },
    });
  };

  return <main className="page page-wide workspace-home">
    <header className="ws-home-head">
      <div>
        <h1>研究工作区</h1>
      </div>
      <div className="ws-home-tools">
        <div className="ws-search">
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索工作区：请输入关键词..." value={query} onChange={event => setQuery(event.target.value)} aria-label="搜索工作区" />
        </div>
        <Select<SortKey> className="ws-select" aria-label="排序" value={sort} onChange={setSort} options={[
          { value: "opened", label: "最近打开" },
          { value: "updated", label: "最近更新" },
          { value: "created", label: "最近创建" },
        ]} />
        <Select<FilterKey> className="ws-select" aria-label="筛选" value={filter} onChange={setFilter} popupMatchSelectWidth={false} options={[
          { value: "all", label: "全部工作区" },
          { value: "filled", label: "有论文" },
          { value: "empty", label: "空工作区" },
        ]} />
      </div>
    </header>
    {notice ? <p className="ws-notice" role="status">{notice}</p> : null}
    <div className="ws-grid">
      <button type="button" className="ws-card ws-card-new" onClick={() => setCreating(true)}>
        <span className="ws-plus" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg></span>
        <strong>新建工作区</strong>
      </button>
      {shown.map(item => {
        const count = Object.keys(item.papers).length;
        return <article key={item.id} className="ws-card">
          <Link className="ws-card-link" to={`/w/${item.id}`}>
            <div className="ws-cover"><WorkspaceCover cover={item.cover} image={item.coverImage} /></div>
            <div className="ws-card-body">
              <h2 title={item.name}>{item.name}</h2>
              <p>{item.description || "\u00a0"}</p>
              <div className="ws-meta">
                <span><FileTextOutlined aria-hidden />{count} 篇论文</span>
                <span><ClockCircleOutlined aria-hidden />最近更新：{relativeTime(projectUpdated(item))}</span>
              </div>
            </div>
          </Link>
          <Dropdown
            trigger={["click"]}
            placement="bottomRight"
            open={menuFor === item.id}
            onOpenChange={open => setMenuFor(current => open ? item.id : current === item.id ? null : current)}
            popupRender={() => <CardMenu item={item} onRename={() => { setMenuFor(null); setRenameId(item.id); }} onCover={cover => { projects.setCover(item.id, cover); setMenuFor(null); }} onUpload={() => { uploadFor.current = item.id; setMenuFor(null); fileRef.current?.click(); }} onDelete={() => remove(item)} />}
          >
            <button type="button" className="ws-more" aria-label={`${item.name}的更多操作`}><EllipsisOutlined /></button>
          </Dropdown>
        </article>;
      })}
      {filteredEmpty ? <p className="ws-filter-empty">没有符合条件的工作区</p> : null}
    </div>
    <CreateWorkspace open={creating} onClose={() => setCreating(false)} />
    <WorkspaceRenameDialog project={list.find(item => item.id === renameId) ?? null} onClose={() => setRenameId(null)} />
    <input ref={fileRef} hidden type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={onCoverFile} />
  </main>;
}
