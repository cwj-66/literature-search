import { useEffect, useState } from "react";
import { Alert, Input, Modal } from "antd";

export function RenameDialog({ open, title, label, value, maxLength, onSave, onClose }: {
  open: boolean; title: string; label: string; value: string; maxLength: number;
  onSave: (name: string) => void | Promise<void>; onClose: () => void;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (open) { setDraft(value); setError(""); } }, [open, value]);
  const name = draft.trim();
  async function save() {
    if (!name || saving) return;
    setSaving(true); setError("");
    try { if (name !== value) await onSave(name); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : "改名失败，请重试"); }
    finally { setSaving(false); }
  }
  return <Modal title={title} open={open} onCancel={() => { if (!saving) onClose(); }} onOk={() => void save()}
    okText="保存" cancelText="取消" confirmLoading={saving} okButtonProps={{ disabled: !name }} cancelButtonProps={{ disabled: saving }}
    closable={!saving} maskClosable={!saving} keyboard={!saving}>
    <label className="rename-field"><span>{label}</span><Input key={open ? value : "closed"} autoFocus aria-label={label} value={draft} maxLength={maxLength} showCount
      disabled={saving} onChange={event => setDraft(event.target.value)}
      onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void save(); } }} /></label>
    {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} /> : null}
  </Modal>;
}
