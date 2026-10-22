import type { ReactNode } from "react";
import { Button } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { formatMessageTime } from "../presentation";

function Spark({ thinking }: { thinking?: boolean }) {
  return <span className={`assistant-spark${thinking ? " is-thinking" : ""}`} aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2.4c.5 3.6 1.8 5.2 5.6 5.8-3.8.6-5.1 2.2-5.6 5.8-.5-3.6-1.8-5.2-5.6-5.8 3.8-.6 5.1-2.2 5.6-5.8Z" /><path fill="currentColor" d="M17.4 13.2c.2 1.5.8 2.2 2.3 2.4-1.5.2-2.1.8-2.3 2.3-.2-1.5-.8-2.1-2.3-2.3 1.5-.2 2.1-.9 2.3-2.4Z" /></svg></span>;
}

function MessageMeta({ time, onCopy, copyLabel, actions }: { time: number | string; onCopy?: () => void; copyLabel?: string; actions?: ReactNode }) {
  return <div className="assistant-msg-meta">
    <time dateTime={new Date(time).toISOString()}>{formatMessageTime(time)}</time>
    {onCopy ? <Button type="text" size="small" className="assistant-copy" aria-label={copyLabel} title={copyLabel} icon={<CopyOutlined />} onClick={onCopy} /> : null}
    {actions}
  </div>;
}
export function AssistantBubble({ time, thinking, children, onCopy, actions }: {
  time: number | string; thinking?: boolean; children: ReactNode; onCopy?: () => void; actions?: ReactNode;
}) {
  return <div className="assistant-msg is-assistant">
    <Spark thinking={thinking} />
    <div className="assistant-msg-body"><div className="assistant-bubble">{children}</div>
      <MessageMeta time={time} onCopy={onCopy} copyLabel="复制回答" actions={actions} />
    </div>
  </div>;
}
export function UserBubble({ time, children, onCopy }: { time: number | string; children: ReactNode; onCopy: () => void }) {
  return <div className="assistant-msg is-user"><div className="assistant-msg-body">
    <div className="assistant-bubble">{children}</div><MessageMeta time={time} onCopy={onCopy} copyLabel="复制问题" />
  </div></div>;
}

