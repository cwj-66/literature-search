"""The chat agent's complete allowlist: planning, search, ranking and coverage."""
from pydantic import BaseModel, ConfigDict
from app.tools.decompose_question import DecomposeQuestionRequest, decompose_question
from app.tools.rerank_papers import RerankPapersRequest, rerank_papers
from app.tools.search_papers import SearchPapersRequest, search_papers
from app.tools.research_scope import ToolContext

class CoverageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

DEFINITIONS = (
    ('decompose_question', 'discovery', '拆解找论文需求：消解指代、确定数量与年份约束、生成围绕原始主题的检索式；不调用论文 API。找论文流程第一步，“继续找/换条件”类请求先经它还原成独立任务；无法确定主题时返回 needs_clarification。', DecomposeQuestionRequest),
    ('search_papers', 'discovery', '按关键词搜索学术论文，返回候选论文 ID、摘要、链接和来源状态；结果是待筛选候选（找论文流程第二步），可能包含相关性不足的条目，需经 rerank_papers 筛选后才能作为推荐；支持年份、来源、排序与分页，不自动导入文献库。中文关键词映射不代表覆盖中文文献库。', SearchPapersRequest),
    ('rerank_papers', 'discovery', '对候选论文去重并按完整检索目标做主题相关性筛选与排序，输出最终推荐、评分、推荐理由、未入选候选与不足原因；不用无关论文凑数。找论文流程最后一步，回答中的推荐必须以此结果为准。', RerankPapersRequest),
    ("assess_coverage", "discovery", "评估当前搜索任务的候选数量与相关性，决定补充检索、翻页、返回结果或停止；仅使用当前任务实际数据。", CoverageArgs),
)
CATALOG = {name: {"name": name, "module": module, "description": description,
                  "parameters": model.model_json_schema()} for name, module, description, model in DEFINITIONS}
MODELS = {name: model for name, _, _, model in DEFINITIONS}
TOOLS = tuple(CATALOG.values())
CHAT_TOOLS = TOOLS
FUNCTION_TOOLS = tuple({"type": "function", "function": {key: item[key] for key in ("name", "description", "parameters")}}
                       for item in CHAT_TOOLS)

async def execute(name: str, arguments: dict, context: ToolContext | None = None, *, state: dict | None = None) -> dict:
    if name not in MODELS:
        raise ValueError("未知聊天研究工具")
    args = MODELS[name].model_validate(arguments).model_dump()
    if name == "decompose_question":
        return await decompose_question(**args)
    if name == "search_papers":
        return await search_papers(**args)
    if name == "rerank_papers":
        return await rerank_papers(**args)
    if state is None:
        raise ValueError("覆盖度评估需要当前搜索任务状态，请在运行中的对话任务内调用")
    from app.agents.tooling import run_coverage_assessment
    assessment, llm_calls, fallback = await run_coverage_assessment(state)
    if assessment.get("next_action") == "read_next":
        assessment = {**assessment, "next_action": "synthesize", "rationale": "按当前标题摘要返回已筛选结果，对话不执行全文阅读"}
    return {"tool": name, **assessment, "llm_calls": llm_calls, "fallback": fallback}
