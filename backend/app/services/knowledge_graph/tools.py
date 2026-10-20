"""Knowledge graph and sample statistics tools; not part of the chat catalog."""
import asyncio
from collections import Counter, defaultdict
from pydantic import BaseModel, ConfigDict, Field
from app.services.analysis import analysis_store
from app.services.knowledge_graph import knowledge_graph
from app.tools.research_scope import ToolContext, resolve

class ScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_ids: list[str] | None = Field(None, min_length=1, max_length=50)

DEFINITIONS = (
    ('query_knowledge_graph', 'knowledge', '在明确论文范围内查询作者、主题、期刊会议、缓存真实引用关系。', ScopeArgs),
    ('analyze_research_trends', 'knowledge', '统计所选样本的年份和已有分析关键词，附样本量和缺失数据，不外推整个领域。', ScopeArgs),
)
CATALOG = {name: {"name": name, "module": module, "description": description,
                  "parameters": model.model_json_schema()} for name, module, description, model in DEFINITIONS}
MODELS = {name: model for name, _, _, model in DEFINITIONS}
TOOLS = tuple(CATALOG.values())

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
        raise ValueError("未知知识图谱工具")
    args = MODELS[name].model_validate(arguments).model_dump()
    papers = await asyncio.to_thread(resolve, context or ToolContext(), args.get("paper_ids"))
    if name == "query_knowledge_graph":
        result = {"status": "completed", **await asyncio.to_thread(knowledge_graph.build, references=[p.local_id for p in papers])}
    else:
        result = await asyncio.to_thread(_trends, papers)
    return {"tool": name, **result}
