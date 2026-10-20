"""Nine validated discovery and knowledge tools, one dispatcher."""

import asyncio
from collections import Counter, defaultdict

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.analysis import analysis_store
from app.services.knowledge_graph import knowledge_graph
from app.services.papers import library
from app.services.retrieval import recommend, search
from app.tools.decompose_question import DecomposeQuestionRequest, decompose_question
from app.tools.rerank_papers import RerankPapersRequest, rerank_papers
from app.tools.search_papers import SearchPapersRequest, search_papers
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


class ScopeArgs(Arguments):
    paper_ids: list[str] | None = Field(None, min_length=1, max_length=50)


DEFINITIONS = (
    ("decompose_question", "discovery", "拆解找论文需求：消解指代、确定数量与年份约束、生成围绕原始主题的检索式；不调用论文 API。找论文流程第一步，“继续找/换条件”类请求先经它还原成独立任务；无法确定主题时返回 needs_clarification。", DecomposeQuestionRequest),
    ("search_papers", "discovery", "按关键词搜索学术论文，返回候选论文 ID、摘要、链接和来源状态；结果是待筛选候选（找论文流程第二步），可能包含相关性不足的条目，需经 rerank_papers 筛选后才能作为推荐；支持年份、来源、排序与分页，不自动导入文献库。中文关键词映射不代表覆盖中文文献库。", SearchPapersRequest),
    ("rerank_papers", "discovery", "对候选论文去重并按完整检索目标做主题相关性筛选与排序，输出最终推荐、评分、推荐理由、未入选候选与不足原因；不用无关论文凑数。找论文流程最后一步，回答中的推荐必须以此结果为准。", RerankPapersRequest),
    ("get_paper_details", "discovery", "获取论文作者、摘要、DOI、年份和原文链接；优先本地数据。", PaperArgs),
    ("find_related_papers", "discovery", "论文相似度搜索：从 arXiv 与 Semantic Scholar 分别召回引用、主题、关键词候选，分组返回依据与评分；临时向量不保存。", RelatedArgs),
    ("get_paper_references", "discovery", "获取论文引用的文献，最多 20 条；来源不可用时明确报告。", PaperArgs),
    ("get_paper_citations", "discovery", "获取引用该论文的文献，最多 20 条；空结果不代表无引用。", PaperArgs),
    ("query_knowledge_graph", "knowledge", "在明确论文范围内查询作者、主题、期刊会议、缓存真实引用关系。", ScopeArgs),
    ("analyze_research_trends", "knowledge", "统计所选样本的年份和已有分析关键词，附样本量和缺失数据，不外推整个领域。", ScopeArgs),
)
CATALOG = {name: {"name": name, "module": module, "description": description,
                  "parameters": model.model_json_schema()} for name, module, description, model in DEFINITIONS}
MODELS = {name: model for name, _, _, model in DEFINITIONS}
CHAT_TOOLS = tuple(CATALOG.values())
FUNCTION_TOOLS = tuple({"type": "function", "function": {key: item[key] for key in ("name", "description", "parameters")}}
                       for item in CHAT_TOOLS)


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


def _analysis(paper):
    task, result = analysis_store.get_state({paper.id, paper.local_id}, paper.local_id)
    return {"paper_id": paper.id, "task": task, "result": result,
            "status": "completed" if result else "missing"}


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
    args = MODELS[name].model_validate(arguments).model_dump()
    if name == "search_papers":
        return await search_papers(**args)
    if name == "decompose_question":
        return await decompose_question(**args)
    if name == "rerank_papers":
        return await rerank_papers(**args)
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
    else:
        papers = await asyncio.to_thread(resolve, ctx, args.get("paper_ids"))
        if name == "query_knowledge_graph":
            result = {"status": "completed", **await asyncio.to_thread(knowledge_graph.build, references=[p.local_id for p in papers])}
        else:
            result = await asyncio.to_thread(_trends, papers)
    return {"tool": name, **result}
