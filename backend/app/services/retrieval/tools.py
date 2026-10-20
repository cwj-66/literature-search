"""Paper relationship tools, available only through the relationship module."""
import asyncio
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.services.papers import library
from app.services.retrieval import recommend, search
from app.tools.research_scope import ToolContext, resolve

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperArgs(Arguments):
    paper_id: str = Field(min_length=1, max_length=500)

    @field_validator("paper_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("论文 ID 不能为空")
        return value.strip()


class RelatedArgs(PaperArgs):
    limit: int = Field(10, ge=1, le=20)

DEFINITIONS = (
    ('get_paper_details', 'relations', '获取论文作者、摘要、DOI、年份和原文链接；优先本地数据。', PaperArgs),
    ('find_related_papers', 'relations', '论文相似度搜索：从 arXiv 与 Semantic Scholar 分别召回引用、主题、关键词候选，分组返回依据与评分；临时向量不保存。', RelatedArgs),
    ('get_paper_references', 'relations', '获取论文引用的文献，最多 20 条；来源不可用时明确报告。', PaperArgs),
    ('get_paper_citations', 'relations', '获取引用该论文的文献，最多 20 条；空结果不代表无引用。', PaperArgs),
)
CATALOG = {name: {"name": name, "module": module, "description": description,
                  "parameters": model.model_json_schema()} for name, module, description, model in DEFINITIONS}
MODELS = {name: model for name, _, _, model in DEFINITIONS}
TOOLS = tuple(CATALOG.values())

async def _paper(ref, ctx, local_only=False):
    paper = await asyncio.to_thread(library.get_paper, ref)
    if ctx.accessible_paper_ids is not None:
        if local_only:
            return (await asyncio.to_thread(resolve, ctx, [ref]))[0]
        if paper is not None:
            try:
                return (await asyncio.to_thread(resolve, ctx, [ref]))[0]
            except ValueError:
                # Public discovery must not return private cached PDF metadata.
                paper = await search.fetch_paper(library.normalize_reference(ref))
                if paper is None:
                    raise ValueError("论文未在本对话授权范围内，请先选择研究资料")
                return paper
    if local_only and (ctx.workspace_id or ctx.current_paper_id or ctx.selected_paper_ids):
        return (await asyncio.to_thread(resolve, ctx, [ref]))[0]
    if paper is None and not local_only:
        paper = await search.fetch_paper(library.normalize_reference(ref))
    if paper is None:
        raise ValueError("没有找到这篇论文")
    return paper

async def execute(name: str, arguments: dict, context: ToolContext | None = None) -> dict:
    if name not in MODELS:
        raise ValueError("未知论文关系工具")
    ctx = context or ToolContext()
    args = MODELS[name].model_validate(arguments).model_dump()
    if name in {"get_paper_details", "find_related_papers", "get_paper_references", "get_paper_citations"}:
        paper = await _paper(args["paper_id"], ctx)
        if name == "get_paper_details":
            result = {"status": "completed", "paper": paper.model_dump(mode="json")}
        elif name == "find_related_papers":
            result = await recommend.discover(paper, args["limit"])
            result["status"] = "partial" if result.get("warnings") else "completed"
        else:
            ref = recommend.s2_paper_ref(paper)
            if not ref or not search.semantic_enabled():
                result = {"status": "unavailable", "papers": [], "warnings": ["当前论文或数据源不支持引用查询。"]}
            else:
                try:
                    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                        items = await search.fetch_s2_links(client, ref, "references" if name == "get_paper_references" else "citations")
                    result = {"status": "completed", "paper_id": paper.id, "papers": [p.model_dump(mode="json") for p in items],
                              "limit": 20, "complete": False, "warnings": ["仅返回来源可用的前 20 条；空列表不表示没有引用。"]}
                except (search.SourceFailure, httpx.HTTPError) as exc:
                    result = {"status": "unavailable", "papers": [], "warnings": [str(exc)]}
    return {"tool": name, **result}
