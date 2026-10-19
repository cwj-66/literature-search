# Agent v1 三工具实现计划（decompose_question / search_papers / rerank_papers）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 agent_v1.md 的三个核心工具做成可独立调用、单独验证的 Agent 工具，打通“拆解问题 → 搜索论文 → 重排序与筛选”链路。

**Architecture:** 核心逻辑放在独立的 `backend/app/tools/` 模块（纯函数式、可单测），Agent 适配层在 `backend/app/agents/tooling.py` 注册自定义 handler 把工具结果写回 state（`queries`/`search_plan`/`recommended_ids`/`recommendation_reasons`）。拆解复用 `attribution.plan_search`；重排复用 `ranking.llm_rank` 打分 + `discovery.required_groups`/`contains` 的确定性 facet 过滤与理由生成。前端不改动，卡片继续由 `recommended_ids` + `recommendation_reasons` 驱动，天然与回答一致。

**Tech Stack:** Python 3 + FastAPI + LangGraph（现有），pydantic v2，unittest.IsolatedAsyncioTestCase + pytest（现有测试风格）。

**Spec:** `agent_v1.md`（工作区根目录）

## Global Constraints

- 复用现有配置、模型与密钥，不新增依赖、不重新配置。
- 三个工具必须可单独调用、单独验证（core 函数不依赖 LangGraph state）。
- 检索式必须围绕用户原始主题，不得扩展成宽泛 search/IR；硬性概念用 `discovery.protect_query` 保护。
- 未指定数量时默认 5 篇；不足时如实返回并说明，禁止用无关论文凑数。
- 候选少于目标篇数时也必须逐篇做相关性筛选，不能跳过筛选全量推荐。
- 推荐理由必须标明依据是标题还是摘要；无摘要如实标记，不编造内容。
- 区分“请求失败”和“成功但无结果”；来源失败信息原样保留，不得包装成搜索成功。
- 评分针对完整用户目标（`resolved_question`），不是针对某次改写后的宽泛检索式。
- v1 不接入全文解析、实验分析、综述写作、引文提取、Judge、知识图谱到找论文流程。
- **工作区存在上一轮未提交的改动（agent.py / discovery.py / prompts.py / state.py / attribution.py / 测试 / 前端等），禁止回滚、禁止 `git add -A`、禁止替用户提交**；本计划全部改动为新增文件 + 对上述文件的最小增量编辑，提交时机由用户决定。
- 测试运行方式：`cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`（必须 cwd=backend）。

## Review Focus

1. 续找类追问（“那继续找”）缺少可用上下文时，必须返回 `needs_clarification` 而不是编造主题 → Task 1 测试。
2. 候选总数少于目标篇数时仍逐篇筛选，不能全量推荐 → Task 2 测试。
3. 候选无摘要时推荐理由如实标记“仅依据标题” → Task 2 测试。
4. 来源失败与相关性不足同时存在时，`shortfall_reason` 要区分两种原因 → Task 3 handler 测试。
5. 助手最终回答、工具推荐与前端卡片必须是同一份 `recommended_ids` → Task 3/5 测试。

---

### Task 1: `decompose_question` 核心工具

**Files:**
- Create: `backend/app/tools/decompose_question.py`
- Test: `backend/tests/test_decompose_question_tool.py`

**Interfaces:**
- Consumes: `app.services.analysis.attribution.plan_search(question: str) -> dict`（返回 `{"queries": [...], "exclusions": [...], "search_plan": {...}, "_llm_used": int}`，已有）；`app.agents.discovery.requested_count(goal: str) -> int | None`（已有，**函数内懒加载导入**以避免 agents↔tools 循环依赖）；`app.infrastructure.llm.complete(messages) -> str`。
- Produces:
  ```python
  class DecomposeQuestionRequest(BaseModel):  # extra="forbid"
      question: str = Field(min_length=1, max_length=2000)
      context: list[str] | None = Field(None, max_length=10)  # 每条约500字符

  async def decompose_question(question: str, context: list[str] | None = None) -> dict
  ```
  返回 dict 固定字段：`{"tool": "decompose_question", "status": "completed" | "needs_clarification", "needs_clarification": bool, "clarification_question": str | None, "resolved_question": str, "target_count": int, "core_topics": list[str], "constraints": {"year_from": int | None, "year_to": int | None}, "exclusions": list[str], "queries": list[str], "llm_calls": int}`。
- Task 3 依赖：handler 将读取返回中的 `resolved_question`/`queries`/`search_plan` 相关内容。

- [ ] **Step 1: 写失败测试** `backend/tests/test_decompose_question_tool.py`

测试用例（unittest 风格，mock 方式照 `backend/tests/test_agent_direct.py`）：
1. `test_default_count_and_queries`：patch `app.services.analysis.attribution.plan_search` 为 AsyncMock 返回 `{"queries": ['("retrieval-augmented generation") AND ("application")'], "exclusions": [], "search_plan": {"core_concepts": ["retrieval-augmented generation"]}, "_llm_used": 1}`；调用 `decompose_question("帮我找有关 RAG 应用的论文")`；断言 `target_count == 5`、`queries == ["(\"retrieval-augmented generation\") AND (\"application\")"]`、`core_topics == ["retrieval-augmented generation"]`、`status == "completed"`、`llm_calls == 1`。
2. `test_explicit_count_and_years`：`decompose_question("帮我找3篇2023到2025年RAG应用的论文")`；断言 `target_count == 3`、`constraints == {"year_from": 2023, "year_to": 2025}`（年份用正则 `(19|20)\d{2}` 提取，≥2 个取 min/max）。
3. `test_continuation_without_context_needs_clarification`：`decompose_question("那继续找")`（匹配续找正则：`^(那?|好|行)?(继续|接着|再|还)` 开头或纯“继续/接着/再来”）；断言 `needs_clarification == True`、`clarification_question` 非空、plan_search mock **未被调用**。
4. `test_continuation_with_context_resolves_topic`：patch `app.infrastructure.llm.complete` 返回 `json.dumps({"resolved_question": "继续找5篇文本向量模型论文", "ambiguous": False})`；`decompose_question("那继续找", context=["用户：帮我找5篇有关向量大模型的论文"])`；断言 plan_search 以 `"继续找5篇文本向量模型论文"` 被调用，`target_count == 5`。
5. `test_continuation_with_useless_context_needs_clarification`：llm.complete 返回 `{"resolved_question": "", "ambiguous": True}`；断言 `needs_clarification == True`。
6. `test_relative_years_not_invented`：`decompose_question("帮我找近三年RAG应用的论文")`；断言 `constraints == {"year_from": None, "year_to": None}`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_decompose_question_tool.py -q`
Expected: FAIL（模块不存在 / ImportError）

- [ ] **Step 3: 实现 `backend/app/tools/decompose_question.py`**

- 续问判定用模块级正则 `CONTINUATION = re.compile(r"^(那|好|行)?(继续|接着|再|还)|(^(继续|接着|再来)$)")`；命中且 `context` 为空 → 直接返回 `needs_clarification`（`clarification_question="想找哪个主题的论文？"`），不调 LLM。
- 命中且 `context` 非空 → 一次 resolver LLM 调用（system prompt：根据对话上下文把简短追问还原成独立检索任务，上下文不足输出 `ambiguous: true`；输出 JSON `{"resolved_question": "...", "ambiguous": true|false}`）；`ambiguous` 或 resolved 为空 → `needs_clarification`。解析失败按 ambiguous 处理。`llm_calls` 计数 +1。
- 未命中续问 → `resolved_question = question.strip()`。
- `target_count = discovery.requested_count(resolved_question) or 5`；`constraints` 从 resolved_question 提取 4 位年份（1 个 → from=to=该年；≥2 → min/max；0 个 → None/None）。
- 懒加载 `from app.services.analysis.attribution import plan_search` 并调用；`queries = planned["queries"]`、`exclusions = planned["exclusions"]`、`core_topics = (planned.get("search_plan") or {}).get("core_concepts") or []`、`llm_calls = planned.get("_llm_used", 0) + resolver 消耗`。
- 整个模块不 import `app.agents` 包内任何模块于顶层（discovery 用函数内 `from app.agents import discovery` 懒加载）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_decompose_question_tool.py -q`
Expected: 6 passed

- [ ] **Step 5: 回归**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过（新增 6 个）

---

### Task 2: `rerank_papers` 核心工具

**Files:**
- Create: `backend/app/tools/rerank_papers.py`
- Test: `backend/tests/test_rerank_papers_tool.py`

**Interfaces:**
- Consumes: `app.services.retrieval.ranking.llm_rank(papers: list, query: str, *, min_candidates: int = 20) -> tuple[list, int]`（已有）；`app.agents.discovery.contains / required_groups`（已有，函数内懒加载）。
- Produces:
  ```python
  class RerankPapersRequest(BaseModel):  # extra="forbid"
      resolved_question: str = Field(min_length=1, max_length=2000)
      candidates: list[dict] = Field(min_length=1, max_length=200)
      target_count: int = Field(5, ge=1, le=50)
      constraints: dict | None = None   # {"year_from": int|None, "year_to": int|None, "exclusions": [str]}
      groups: list[list[str]] | None = None  # 硬性 facet 组；None 时函数内用 required_groups(resolved_question)

  async def rerank_papers(resolved_question: str, candidates: list[dict], target_count: int = 5,
                          constraints: dict | None = None, groups: list[list[str]] | None = None) -> dict
  ```
  返回 dict 固定字段：`{"tool": "rerank_papers", "status": "completed", "recommended": [ {**paper, "score": float | None, "reason": str} ], "recommended_ids": [str], "scores": {id: score}, "reasons": {id: reason}, "remaining_candidates": [str], "enough": bool, "shortfall_reason": str | None, "filtered_out_count": int, "llm_calls": int}`。
- Task 3 依赖：handler 将读取 `recommended_ids`/`reasons`/`enough`/`shortfall_reason`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_rerank_papers_tool.py`

候选构造辅助：dict 列表，字段 `id/title/abstract/year`。mock `app.services.retrieval.ranking.llm_rank`（patch 其返回 `(papers_with_rank_score, 1)`，给每篇注入 `rank_score`）。

1. `test_relevance_filter_excludes_off_topic`：`resolved_question="帮我找3篇有关RAG应用的论文"`，`target_count=3`；候选含 1 篇免疫学 RAG 基因论文（abstract 无 retrieval/language model 词）；断言它被过滤（不在 `recommended_ids`）。
2. `test_embedding_topic_rejects_generic_ir`：`resolved_question="帮我找5篇有关向量大模型的论文"`；候选 2 篇 text embedding 论文 + 1 篇 generic web search IR（无 embedding 词）；断言 `recommended_ids` 只含 embedding 论文（facet 组来自 `required_groups`，函数内自动推导）。
3. `test_shortfall_not_filled_with_unrelated`：6 篇候选只有 2 篇通过 facet 过滤，`target_count=5`；断言 `len(recommended) == 2`、`enough == False`、`shortfall_reason` 含“仅 2 篇”。
4. `test_no_abstract_marked_in_reason`：候选无 `abstract` 字段；断言其 `reason` 含“无摘要，仅依据标题”。
5. `test_year_constraint_filter`：`constraints={"year_from": 2023, "year_to": 2024, "exclusions": []}`；2020 年候选被过滤。
6. `test_low_rank_score_rejected`：llm_rank 返回某篇 `rank_score=4`；断言该篇不在 `recommended_ids`（沿用现有 `<7` 拒绝约定）。
7. `test_remaining_and_consistency`：断言 `remaining_candidates` = 通过过滤未入选的 id（截断 20 条），`recommended` 每项含 `score` 与 `reason`，`len(recommended_ids) == len(recommended)`。
8. `test_fewer_candidates_than_target_still_filtered`：候选共 2 篇（1 篇跑题），`target_count=5`；断言跑题篇不进 `recommended_ids`、`enough == False`（验收 #4：不跳过筛选）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_rerank_papers_tool.py -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 `backend/app/tools/rerank_papers.py`**

流程（按序）：
1. 按 `id` 去重（保序）。
2. `groups = groups if groups is not None else discovery.required_groups(resolved_question)`（懒加载 discovery）。
3. 确定性预过滤（`filtered_out_count` 统计被剔除数）：`constraints` 年份范围；`constraints.exclusions` 用 `discovery.contains` 命中标题+摘要则剔除；每个 facet 组必须在 `title + " " + abstract` 中至少命中一词（`discovery.contains`），否则剔除；问题含 RAG 术语且文本不匹配 `retriev\w*|language models?|\bLLMs?\b|generative (?:AI|models?)|检索增强` 时剔除（与 `discovery.select_bibliography` 同一免疫学防护）。
4. LLM 打分：`ranked, llm_delta = await llm_rank(survivors, resolved_question, min_candidates=2)`；再剔除 `rank_score < 7` 的（`rank_score is not None` 时）。
5. 选取：保持 llm_rank 顺序（已按分数降序、未打分附后）取前 `target_count`。
6. `reason` 生成：对每个 facet 组取命中词 `hits`；`hits` 非空 → `"标题" + ("与摘要" if 有命中词不在标题 else "") + "匹配：" + "、".join(hits)`；`abstract` 为空 → 追加 `"（该论文无摘要，仅依据标题判断）"`；`hits` 为空（无 facet 组）→ `f"大模型相关性评分 {score}/10"`（score 为 None 时 `"未获得评分，保留待人工判断"`）。
7. `enough = len(recommended) >= target_count`；不足时 `shortfall_reason = f"候选 {len(candidates)} 篇中仅 {len(recommended)} 篇通过主题相关性筛选，未用无关论文凑数"`；`recommended` 为空时改为 `"没有候选通过主题相关性筛选"`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_rerank_papers_tool.py -q`
Expected: 8 passed

- [ ] **Step 5: 回归**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过

---

### Task 3: 注册工具与 Agent handler（state 适配层）

**Files:**
- Modify: `backend/app/tools/research.py`（DEFINITIONS + execute 分发，~75-96、181-191 行处）
- Modify: `backend/app/agents/tooling.py`（LLM_TOOLS:55、TOOL_REGISTRY:881-889、新增两个 handler，仿 `_tool_search_papers`）
- Test: `backend/tests/test_agent_v1_tools.py`

**Interfaces:**
- Consumes: Task 1/2 的 core 函数与 Request 模型；`app.agents.discovery.required_groups / requested_count / protect_query`；state 字段 `papers`/`goal`/`queries`/`search_plan`/`year_from`/`year_to`/`source_errors`。
- Produces（Task 4/5 依赖）：
  - `tooling._tool_decompose_question(state: dict, args: dict) -> tuple[str, dict]`
  - `tooling._tool_rerank_papers(state: dict, args: dict) -> tuple[str, dict]`
  - state 更新键：`queries`、`search_plan`（附 `resolved_question`/`target_count`）、`recommended_ids`、`recommendation_reasons`、`llm_calls`、`events_extra`、`past_extra`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_agent_v1_tools.py`

1. `test_decompose_handler_writes_state`：构造 `state = {"goal": "帮我找5篇有关向量大模型的论文", "messages": [], "queries": [], "llm_calls": 0, "year_from": None, "year_to": None}`；patch `attribution.plan_search`；调 `tooling._tool_decompose_question(state, {"question": state["goal"]})`；断言 updates 含 `queries`（每条经 `protect_query` 处理）、`search_plan["resolved_question"]`、`search_plan["target_count"] == 5`、`llm_calls` 增加。
2. `test_decompose_handler_clarification_writes_nothing`：`args={"question": "那继续找"}`；断言 updates 无 `queries`/`search_plan`，content JSON 的 `needs_clarification == True`。
3. `test_rerank_handler_updates_recommendation_state`：state 含两篇候选 `papers`；patch `ranking.llm_rank`；调 `tooling._tool_rerank_papers(state, {"resolved_question": "帮我找5篇有关向量大模型的论文"})`；断言 updates 的 `recommended_ids`/`recommendation_reasons` 与 core 返回一致。
4. `test_rerank_handler_empty_pool_errors`：`papers` 为空；断言 content JSON 有 `error` 且 updates 不含 `recommended_ids`。
5. `test_rerank_handler_shortfall_mentions_source_errors`：state `source_errors={"semantic_scholar": "timeout"}`、候选不足；断言 content JSON 的 `shortfall_reason` 含来源失败提示（如“部分来源调用失败”）。
6. `test_tools_registered_and_budgeted`：断言 `research.CATALOG` 含 `decompose_question`/`rerank_papers` 且 module 为 `"discovery"`；`TOOL_REGISTRY` 含两者且对应自定义 handler；两者 ∈ `LLM_TOOLS`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent_v1_tools.py -q`
Expected: FAIL（AttributeError / 断言失败）

- [ ] **Step 3: 注册 catalog**（`backend/app/tools/research.py`）

- import 两个新工具模块与 Request 模型；
- DEFINITIONS 追加：
  - `("decompose_question", "discovery", "拆解找论文需求：消解指代、确定数量与年份约束、生成围绕原始主题的检索式；不调用论文 API。找论文流程第一步，“继续找/换条件”类请求先经它还原成独立任务；无法确定主题时返回 needs_clarification。", DecomposeQuestionRequest)`
  - `("rerank_papers", "discovery", "对候选论文去重并按完整检索目标做主题相关性筛选与排序，输出最终推荐、评分、推荐理由、未入选候选与不足原因；不用无关论文凑数。找论文流程最后一步，回答中的推荐必须以此结果为准。", RerankPapersRequest)`
- `execute()` 开头分发处追加：`if name == "decompose_question": return await decompose_question(**args)`、`if name == "rerank_papers": return await rerank_papers(**args)`（放在 `search_papers` 分支之后）。

- [ ] **Step 4: 实现 handler 并注册**（`backend/app/agents/tooling.py`）

- `LLM_TOOLS = {"analyze_paper", "request_deep_analysis", "decompose_question", "rerank_papers"}`。
- `_tool_decompose_question(state, args)`：
  - `args` 用 `research_tools.MODELS["decompose_question"].model_validate` 校验；
  - `context` 缺省时从 `state["messages"]` 取最近 3 条 user 消息文本（截断 500 字符/条）；
  - 调 core；`needs_clarification` → 直接返回 `(json.dumps(report), {})`；
  - 否则 `plan = report 的 search_plan 部分`（core 返回不含 search_plan 整体时，handler 需重新组织：handler 侧自行再取——**实现约定**：core 返回中加透传 `"search_plan": planned.get("search_plan") or {}` 字段，Task 1 的返回字段清单补上此项）；
  - `protected = [protect_query(q, report["resolved_question"], plan) for q in report["queries"]]`；
  - updates：`queries=list(dict.fromkeys([*(state.get("queries") or []), *protected]))`、`search_plan={**plan, "resolved_question": ..., "target_count": ...}`、`llm_calls=state.get("llm_calls",0)+report["llm_calls"]`、`events_extra=[{"step":"tool","name":"decompose_question","summary":report["status"]}]`、`past_extra=[["decompose_question", report["status"]]]`；
  - content：`compact_tool_content` 不适用（仅 search_papers），直接 `json.dumps(report)`。
- `_tool_rerank_papers(state, args)`：
  - `resolved_question = args.get("resolved_question") or state.get("goal")`；`target_count = args.get("target_count") or discovery.requested_count(goal) or 5`（tooling 已 import discovery 吗——未则在函数内懒加载）；
  - candidates 为空 → `return json.dumps({"tool":"rerank_papers","status":"failed","error":"没有候选论文，请先调用 search_papers"}), {"events_extra":[...],"past_extra":[["rerank_papers","failed"]]}`；
  - `constraints = args.get("constraints") or {"year_from": state.get("year_from"), "year_to": state.get("year_to"), "exclusions": []}`；`groups = required_groups(resolved_question, state.get("search_plan"))`；
  - 调 core；`shortfall_reason` 在 `not enough and state.get("source_errors")` 时追加 `"；部分来源调用失败：..."`（截取各错误信息前 80 字符）；
  - updates：`recommended_ids=report["recommended_ids"]`、`recommendation_reasons=report["reasons"]`、`llm_calls` 累加、`papers`（把 `rank_score` 合并回对应候选）、`events_extra`/`past_extra`（summary 用 `"enough"`/`"shortfall"`）；
  - content：`json.dumps({k: report[k] for k in ("tool","status","recommended_ids","enough","shortfall_reason","remaining_candidates","filtered_out_count")} | {"recommended": [{"id": p["id"], "title": p.get("title"), "year": p.get("year"), "score": p.get("score"), "reason": p.get("reason")} for p in report["recommended"]]})`。
- `TOOL_REGISTRY` 追加 `"decompose_question": _tool_decompose_question, "rerank_papers": _tool_rerank_papers`（放在 catalog 展开之后，覆盖默认 catalog handler）。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent_v1_tools.py -q`
Expected: 6 passed

- [ ] **Step 6: 回归**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过

---

### Task 4: Prompt 编排（让 Agent 按 拆解→搜索→重排 流程走）

**Files:**
- Modify: `backend/app/agents/prompts.py`（REACT_SYSTEM:53-82、DOMAIN_PROMPTS["discovery"]:87-90、AGENT_PROMPT_VERSION:10）
- Modify: `backend/app/tools/research.py`（search_papers 的 DEFINITIONS 描述，~76 行）
- Test: `backend/tests/test_agent_v1_prompts.py`

**Interfaces:**
- Consumes: Task 3 的工具名（`decompose_question`/`search_papers`/`rerank_papers`）。
- Produces: 仅 prompt 文本与版本号变更，无代码接口。

- [ ] **Step 1: 写失败测试** `backend/tests/test_agent_v1_prompts.py`

1. `test_react_system_mentions_v1_flow`：`from app.agents.prompts import REACT_SYSTEM, DOMAIN_PROMPTS, AGENT_PROMPT_VERSION`；断言 `REACT_SYSTEM` 同时含 `"decompose_question"`、`"rerank_papers"` 和“必须与 rerank_papers 返回的 recommended 一致”语义的句子（断言子串 `"recommended 一致"`）；`DOMAIN_PROMPTS["discovery"]` 含 `decompose_question` 与 `rerank_papers`；`AGENT_PROMPT_VERSION` 以 `"v15"` 开头。
2. `test_catalog_descriptions_mention_flow`：`research.CATALOG["decompose_question"]` 的描述含 `"不调用论文 API"`；`research.CATALOG["rerank_papers"]` 描述含 `"不足原因"`；`research.CATALOG["search_papers"]` 描述含 `"筛选"`（提示候选需再筛选）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent_v1_prompts.py -q`
Expected: FAIL

- [ ] **Step 3: 改 prompt**

- `AGENT_PROMPT_VERSION = "v15-agent-v1-tools"`。
- `REACT_SYSTEM`【工具使用】段追加一条规则（编号接现有 7 条之后）：
  “找论文类目标走三步：decompose_question 拆解（数量默认 5），search_papers 检索候选（可一次发多个不同 query 调用；补搜最多 2 次，可用同主题新检索式或翻 page），候选足够后 rerank_papers 得到最终推荐；最终 answer 列出的论文必须与 rerank_papers 返回的 recommended 一致，不得自行增删或换序。rerank_papers 返回 enough=false 时，如实说明不足原因，禁止用候选池里的其他论文凑数。”
- `DOMAIN_PROMPTS["discovery"]` 开头追加：
  “【找论文流程】decompose_question → search_papers → rerank_papers。追问上一轮结果（“是找不到吗/为什么只有两篇”）属于 discuss，不在本模式重搜；用户明确要求继续找、换条件时才再走流程，且主题沿用 decompose_question 还原的 resolved_question。”
- `research.py` DEFINITIONS 中 `search_papers` 描述改为：`"搜索 arXiv、Semantic Scholar，保留来源错误和分页信息；结果是待筛选候选（找论文流程第二步），需经 rerank_papers 筛选后才能作为推荐。"`；两个新工具描述按 Task 3 Step 3 所引文本落字。

- [ ] **Step 4: 运行测试确认通过 + 回归**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过

---

### Task 5: 端到端链路验证（手动驱动 react 循环）

**Files:**
- Test: `backend/tests/test_agent_v1_flow.py`

**Interfaces:**
- Consumes: Task 1-4 全部；`agent.react_agent` / `agent.tool_node` / `agent.initial_state`（agent.py 导出，测试可调用）。
- Produces: 无新接口。

- [ ] **Step 1: 写失败测试** `backend/tests/test_agent_v1_flow.py`

`test_find_papers_three_step_flow`：
- `state = agent.initial_state("帮我找三个有关 RAG 应用的论文", context={"accessible_paper_ids": []})`，`state.update({"mode": "react", "task_kind": "research", "domains": ["*"]})`。
- patch：`attribution.plan_search`（返回 RAG 应用 plan）、`search.search_papers`（返回 4 篇候选 dict，含 1 篇跑题）、`ranking.llm_rank`（给候选打 `rank_score`，跑题篇低分或 facet 过滤）。
- `llm.complete` side_effect 按序返回 react_agent 的 4 轮输出：
  1. `{"thought":"拆解","tool_calls":[{"name":"decompose_question","args":{"question":"帮我找三个有关 RAG 应用的论文"}}]}`
  2. `{"thought":"检索","tool_calls":[{"name":"search_papers","args":{"query":"(\"retrieval-augmented generation\") AND (\"application\")","page_size":10}},{"name":"search_papers","args":{"query":"(\"retrieval augmented generation\") AND (\"real-world\")","page_size":10}}]}`
  3. `{"thought":"筛选","tool_calls":[{"name":"rerank_papers","args":{"resolved_question":"帮我找三个有关 RAG 应用的论文","target_count":3}}]}`
  4. `{"thought":"完成","answer":"推荐 3 篇：arxiv:rag1、arxiv:rag2、arxiv:rag3。"}`
  （注意 tool_calls 消息格式以 `agent.react_agent` 内部转换为准——`ReactOutput` 字段为 `thought`/`tool_calls`/`answer`，参考 agent.py:560-563 的既有契约。）
- 手动循环（≤6 轮）：`out = await agent.react_agent(state)`；合并 state；最后一条消息含 tool_calls 则 `t = await agent.tool_node(state)` 合并，否则 break。
- 断言：`state["recommended_ids"]` 长度 3 且全部通过 RAG facet 过滤；最后一轮 answer 字符串与 `recommended_ids` 一致（answer 由 mock 固定，断言其中出现的 id 集合 == set(recommended_ids)）；`state["queries"]` 非空且包含 protect_query 处理后的检索式；跑题候选不在 `recommended_ids`；`llm_calls`/`api_calls` 计数与 mock 调用次数吻合。
- 另加 `test_discuss_followup_does_not_research`：走 `agent.run("是找不到吗", context={"conversation_history": "..."})`（照抄 test_agent_direct.py:23 的模式，patch 研究节点为 AssertionError），断言不触发 tool_node。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent_v1_flow.py -q`
Expected: FAIL

- [ ] **Step 3: 修复实现直至通过**

若链路暴露工具/handler 缺陷，回到 Task 1-3 修最小改动，不许为测试改语义。

- [ ] **Step 4: 全量回归**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过

- [ ] **Step 5: 真实 API 冒烟（验收 #5，需本机 .env 密钥与网络）**

启动后端，依次发送：“帮我找三个有关 RAG 应用的论文”“帮我找 5 篇有关向量大模型的论文”，随后追问“是找不到吗”。人工核对：推荐确实围绕主题；追问解释不触发新搜索；前端卡片数与回答一致。若当前环境无法联网或缺密钥，记录为“待用户环境验证”，不得声称已完成。

---

## Self-Review 记录

- 规格逐条覆盖：三个工具定义（T1/T2/T3）、注册与描述（T3/T4）、典型流程与补搜 2 次上限（T4 prompt + 现有 `MAX_EXTRA_SEARCH=2` 机制）、追问不重启搜索（T5 test_discuss_followup + 现有 router discuss 改动）、来源失败诚实返回（T2 shortfall + T3 handler）、前端一致（T3 写 `recommended_ids`/`recommendation_reasons`，无前端改动）、可独立验证（T1/T2 为无 state 依赖的 core 函数）。最小验收 1-4 由 T2/T5 测试覆盖；验收 5 的自动化部分在 T3/T5，真实 API 部分为 T5 Step 5。
- 已修正：T1 返回字段补充 `search_plan` 透传（T3 handler 需要）；T3 handler 的 state 更新键与 state.py 现有字段核对无误（`queries`/`search_plan`/`recommended_ids`/`recommendation_reasons`/`llm_calls` 均已存在于 AgentState）。
- 明确不做：不改 `state.py`、不改前端、不动现有 plan/bibliography 确定性流程（用户已确认）。
