"""Four business modules, fourteen validated chat tools, one dispatcher."""

import asyncio
from collections import Counter, defaultdict
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.analysis import analysis_store
from app.services.knowledge_graph import knowledge_graph
from app.services.papers import library, workspaces
from app.services.retrieval import recommend, search
from app.tools import analyze_paper as analyze_tool, index_paper as index_tool
from app.tools.add_papers_to_workspace import AddPapersToWorkspaceRequest, add_papers_to_workspace
from app.tools.search_papers import SearchPapersRequest, search_papers
from app.tools.research_scope import ToolContext, resolve
from app.tools import paper_evidence


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


class WorkspaceArgs(Arguments):
    workspace_id: str | None = Field(None, min_length=1, max_length=80)


class RemoveArgs(WorkspaceArgs):
    paper_id: str = Field(min_length=1, max_length=500)


class ScopeArgs(Arguments):
    paper_ids: list[str] | None = Field(None, min_length=1, max_length=50)


class RetrieveArgs(ScopeArgs):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("检索问题不能为空")
        return value.strip()


class ContextArgs(ScopeArgs):
    chunk_id: str = Field(min_length=1, max_length=700)
    neighbors: int = Field(1, ge=0, le=3)


class AnalyzeArgs(ScopeArgs):
    paper_ids: list[str] = Field(min_length=1, max_length=5)
    fields: list[str] | None = None
    depth: Literal["standard", "deep"] = "standard"


DEFINITIONS = (
    ("search_papers", "discovery", "搜索 arXiv、Semantic Scholar，保留来源错误和分页信息。", SearchPapersRequest),
    ("get_paper_details", "discovery", "获取论文作者、摘要、DOI、年份和原文链接；优先本地数据。", PaperArgs),
    ("find_related_papers", "discovery", "按语义、主题和真实引用记录推荐相关论文，附评分与来源限制。", RelatedArgs),
    ("get_paper_references", "discovery", "获取论文引用的文献，最多 20 条；来源不可用时明确报告。", PaperArgs),
    ("get_paper_citations", "discovery", "获取引用该论文的文献，最多 20 条；空结果不代表无引用。", PaperArgs),
    ("list_workspace_papers", "workspace", "列出当前工作区成员、论文 ID、全文、索引和分析状态。", WorkspaceArgs),
    ("add_papers_to_workspace", "workspace", "将论文加入已有工作区，并排队后台全文获取、解析和索引；重复加入不会重复处理。", AddPapersToWorkspaceRequest),
    ("remove_paper_from_workspace", "workspace", "仅移除工作区成员关系，保留共用论文、原文和索引。", RemoveArgs),
    ("analyze_paper", "analysis", "提取研究问题、方法、创新、实验和局限；优先复用已有分析。", AnalyzeArgs),
    ("get_paper_analysis", "analysis", "只读已有结构化分析，缺失时返回 missing，不调用大模型。", PaperArgs),
    ("retrieve_paper_evidence", "analysis", "在明确的论文范围内检索已有文本和向量，返回原文、文本块 ID、页码、相关性。", RetrieveArgs),
    ("read_paper_context", "analysis", "读取命中文本块相邻内容，保持原论文归属和页码，不自动解析或索引。", ContextArgs),
    ("query_knowledge_graph", "knowledge", "在明确论文范围内查询作者、主题、期刊会议、缓存真实引用关系。", ScopeArgs),
    ("analyze_research_trends", "knowledge", "统计所选样本的年份和已有分析关键词，附样本量和缺失数据，不外推整个领域。", ScopeArgs),
)
CATALOG = {name: {"name": name, "module": module, "description": description,
                  "parameters": model.model_json_schema()} for name, module, description, model in DEFINITIONS}
MODELS = {name: model for name, _, _, model in DEFINITIONS}
CHAT_TOOLS = tuple(CATALOG.values())
FUNCTION_TOOLS = tuple({"type": "function", "function": {key: item[key] for key in ("name", "description", "parameters")}}
                       for item in CHAT_TOOLS)


def _workspace(args, ctx):
    explicit = args.get("workspace_id")
    if explicit and ctx.workspace_id and explicit != ctx.workspace_id:
        raise ValueError("目标工作区与当前聊天工作区不一致")
    workspace_id = ctx.workspace_id or explicit
    if not workspace_id:
        raise ValueError("请指定当前 workspace_id")
    workspaces.require(workspace_id)
    return workspace_id


async def _paper(ref, ctx, local_only=False):
    paper = await asyncio.to_thread(library.get_paper, ref)
    if local_only and (ctx.workspace_id or ctx.current_paper_id or ctx.selected_paper_ids):
        return (await asyncio.to_thread(resolve, ctx, [ref]))[0]
    if paper is None and not local_only:
        paper = await search.fetch_paper(library.normalize_reference(ref))
    if paper is None:
        raise ValueError("没有找到这篇论文")
    return paper


def _analysis(paper):
    task, result = analysis_store.get_state({paper.id, paper.local_id}, paper.local_id)
    return {"paper_id": paper.id, "task": task, "result": result,
            "status": "completed" if result else "missing"}


def _list(workspace_id):
    result = workspaces.list_papers(workspace_id)
    from app.services.papers import documents
    for row in result["papers"]:
        paper = library.get_paper(row["paper"]["local_id"])
        row["index"] = index_tool.get_index_status(paper.local_id) or index_tool.get_index_status(paper.id)
        info = _analysis(paper)
        row["analysis"] = {"status": info["status"], "task": info["task"]}
        row["fulltext_available"] = bool(documents.get_text(paper.local_id, paper.id))
        from app.services.papers import processing
        row["processing"] = processing.status(paper.local_id)
    return {"status": "completed", **result}


def _trends(papers):
    years, themes, by_year = Counter(), Counter(), defaultdict(Counter)
    analyzed = 0
    for paper in papers:
        if paper.year is not None:
            years[paper.year] += 1
        result = _analysis(paper)["result"]
        if not result:
            continue
        analyzed += 1
        for term in set(result.get("keywords") or []):
            if term not in ("未知", "论文未明确说明"):
                themes[term] += 1
                if paper.year is not None:
                    by_year[paper.year][term] += 1
    warnings = ["统计仅描述当前论文样本，不能代表整个领域；主题来自已有 AI 分析。"]
    if len(papers) < 20 or len(years) < 3:
        warnings.append("样本不足 20 篇或不足 3 个年份，仅返回描述统计，不推断研究趋势。")
    return {"status": "partial", "sample_size": len(papers), "analyzed_count": analyzed,
            "missing_year_count": len(papers) - sum(years.values()), "missing_analysis_count": len(papers) - analyzed,
            "year_distribution": [{"year": year, "count": count} for year, count in sorted(years.items())],
            "topics": [{"keyword": term, "count": count} for term, count in themes.most_common(30)],
            "topics_by_year": [{"year": year, "topics": dict(counts.most_common(15))} for year, counts in sorted(by_year.items())],
            "paper_ids": [p.id for p in papers], "warnings": warnings}


async def execute(name: str, arguments: dict, context: ToolContext | None = None) -> dict:
    if name not in MODELS:
        raise ValueError("未知聊天研究工具")
    ctx = context or ToolContext()
    # Workspace defaults are injected before validating required parameters.
    args = dict(arguments)
    if name == "add_papers_to_workspace" and "workspace_id" not in args and ctx.workspace_id:
        args["workspace_id"] = ctx.workspace_id
    args = MODELS[name].model_validate(args).model_dump()
    if name == "search_papers":
        return await search_papers(**args)
    if name == "list_workspace_papers":
        result = await asyncio.to_thread(_list, _workspace(args, ctx))
    elif name == "add_papers_to_workspace":
        workspace_id = _workspace(args, ctx)
        result = await add_papers_to_workspace(workspace_id, args["paper_ids"])
        from app.services.papers import processing
        for item in result["results"]:
            if item["status"] in ("added", "already_exists"):
                item["processing"] = await asyncio.to_thread(processing.ensure_queued, item["local_id"], True)
        stages = [item["processing"]["status"] for item in result["results"] if item.get("processing")]
        result["processing_summary"] = {stage: stages.count(stage) for stage in set(stages)}
    elif name == "remove_paper_from_workspace":
        result = await asyncio.to_thread(workspaces.remove, _workspace(args, ctx), [args["paper_id"]])
    elif name in {"get_paper_details", "find_related_papers", "get_paper_references", "get_paper_citations", "get_paper_analysis"}:
        paper = await _paper(args["paper_id"], ctx, name == "get_paper_analysis")
        if name == "get_paper_details":
            result = {"status": "completed", "paper": paper.model_dump(mode="json")}
        elif name == "get_paper_analysis":
            result = await asyncio.to_thread(_analysis, paper)
        elif name == "find_related_papers":
            # Existing recommendation service needs a local seed and caches its scoring results.
            if not getattr(paper, "local_id", None):
                raise ValueError("推荐前请先将种子论文加入工作区")
            result = await recommend.recommendations(paper.local_id, args["limit"], False)
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
    else:
        papers = await asyncio.to_thread(resolve, ctx, args.get("paper_ids"))
        if name == "analyze_paper":
            if len(papers) > 5:
                raise ValueError("一次最多分析 5 篇论文，请缩小已选范围")
            result = await analyze_tool.analyze_paper([p.local_id for p in papers], args["fields"], args["depth"], ctx.workspace_id)
        elif name == "retrieve_paper_evidence":
            result = await paper_evidence.retrieve(papers, args["query"], args["limit"])
            unindexed = set(result.get("unindexed_paper_ids") or [])
            if unindexed:
                from app.services.papers import processing
                result["processing"] = []
                for p in papers:
                    if p.local_id in unindexed:
                        row = await asyncio.to_thread(processing.status, p.local_id) or {}
                        result["processing"].append({"paper_id": p.id, "local_id": p.local_id,
                                                     "status": row.get("status"), "message": row.get("message")})
        elif name == "read_paper_context":
            result = await asyncio.to_thread(paper_evidence.context, papers, args["chunk_id"], args["neighbors"])
        elif name == "query_knowledge_graph":
            result = {"status": "completed", **await asyncio.to_thread(knowledge_graph.build, references=[p.local_id for p in papers])}
        else:
            result = await asyncio.to_thread(_trends, papers)
    return {"tool": name, **result}
