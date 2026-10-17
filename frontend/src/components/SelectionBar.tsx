import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { selection, useSelection } from "../selection";

export function SelectionBar() {
  const items = useSelection();
  const navigate = useNavigate();
  if (!items.length) return null;
  const comparable = items.length >= 3 && items.length <= 5;
  return <div className="selection-bar" role="region" aria-label="已选论文操作">
    <div className="selection-bar-inner">
      <span className="selection-count">已选 <strong>{items.length}</strong> 篇</span>
      <span className="selection-hint">{comparable ? "可以生成对比表" : items.length < 3 ? "对比需选 3–5 篇" : "对比最多 5 篇"}</span>
      <div className="selection-actions">
        <Button type="text" onClick={() => selection.clear()}>清除</Button>
        <Button disabled={!comparable} onClick={() => navigate("/studio?tab=compare")}>对比</Button>
        <Button type="primary" onClick={() => navigate("/studio?tab=ask&scope=selected")}>对所选提问</Button>
      </div>
    </div>
  </div>;
}
