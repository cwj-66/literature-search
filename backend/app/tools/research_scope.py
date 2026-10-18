"""Resolve explicit research scopes. Empty scopes never mean the whole database."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.papers import library, workspaces


class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str | None = Field(None, min_length=1, max_length=80)
    scope: Literal["current", "selected", "workspace"] = "selected"
    current_paper_id: str | None = Field(None, min_length=1, max_length=500)
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=50)
    accessible_paper_ids: list[str] | None = Field(None, max_length=500)
    conversation_history: str = Field("", max_length=16000)


def resolve(context: ToolContext, requested: list[str] | None = None):
    # An explicit conversation grant may span workspaces. Empty means no access.
    if context.accessible_paper_ids is not None:
        allowed = {}
        for ref in dict.fromkeys(context.accessible_paper_ids):
            paper = library.get_paper(ref)
            if paper is None:
                raise ValueError(f"论文未入库：{ref}")
            allowed[paper.local_id] = paper
        if not requested:
            return list(allowed.values())
        result = []
        for ref in dict.fromkeys(requested):
            paper = library.get_paper(ref)
            if paper is None or paper.local_id not in allowed:
                raise ValueError(f"论文超出本对话可访问范围：{ref}")
            if paper not in result:
                result.append(paper)
        return result
    members = None
    if context.workspace_id:
        members = {row["paper"]["local_id"] for row in workspaces.list_papers(context.workspace_id)["papers"]}
    if context.scope == "workspace":
        if members is None:
            raise ValueError("查询整个工作区时必须提供 workspace_id")
        base = [row["paper"]["local_id"] for row in workspaces.list_papers(context.workspace_id)["papers"]]
    elif context.scope == "current":
        if not context.current_paper_id:
            raise ValueError("请先指定当前论文")
        base = [context.current_paper_id]
    else:
        base = context.selected_paper_ids or requested or []
    if not base:
        if context.scope == "workspace":
            return []
        raise ValueError("请指定当前论文、已选论文，或明确选择整个工作区")
    papers = []
    for ref in dict.fromkeys(base):
        paper = library.get_paper(ref)
        if paper is None:
            raise ValueError(f"论文未入库：{ref}")
        if members is not None and paper.local_id not in members:
            raise ValueError(f"论文不属于当前工作区：{ref}")
        papers.append(paper)
    if requested:
        allowed = {p.local_id for p in papers}
        narrowed = []
        for ref in dict.fromkeys(requested):
            paper = library.get_paper(ref)
            if paper is None or paper.local_id not in allowed:
                raise ValueError(f"论文超出当前研究范围：{ref}")
            narrowed.append(paper)
        papers = narrowed
    return papers
