"""Agent 核心测试：分支规则、HITL 计划审批、业务工具与 Judge 质量检查。"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents import agent
from app.infrastructure import llm
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


def route_plan():
    """router_node 消耗的首个 LLM 调用：路由决策（plan）。"""
    return json.dumps({"mode": "plan", "rationale": "调研性质，需要规划"})


def rj(*tool_calls, answer=None, thought="t"):
    """react_agent 的 LLM 输出：tool_calls 或 answer 二选一。"""
    payload = {"thought": thought}
    if tool_calls:
        payload["tool_calls"] = [{"name": name, "args": args} for name, args in tool_calls]
    else:
        payload["answer"] = answer
    return json.dumps(payload, ensure_ascii=False)


def claims_json(*claims):
    return json.dumps({"claims": [{"text": text, "evidence_ids": eids} for text, eids in claims]})


def judge_json(*reviews):
    return json.dumps({"reviews": list(reviews)})


def verdict(claim_id, outcome="supported", issue=None, action="keep"):
    return {"claim_id": claim_id, "verdict": outcome, "issue": issue, "action": action, "quote": ""}


def found(n, start=1):
    papers = [paper(i, abstract=f"abstract {i} about RAG hallucination mitigation.").model_dump() for i in range(start, start + n)]
    return {"papers": papers, "source_errors": {}, "source_retry_after": {}}


EMPTY_SEARCH = {"papers": [], "source_errors": {}, "source_retry_after": {}}


def planned(*queries):
    return {"queries": list(queries), "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}


class AgentBranchTests(unittest.TestCase):
    def test_coverage_gap_expands_instead_of_stopping(self):
        state = agent.initial_state("降低RAG幻觉")
        state.update({"papers": [p.model_dump() for p in (paper(1), paper(2))], "queries": ["q1", "q2"], "query_index": 0})
        self.assertEqual(agent.route(state), "expand")

    def test_rate_limit_without_candidates_stops(self):
        state = agent.initial_state("降低RAG幻觉")
        state["source_errors"] = {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"}
        self.assertEqual(agent.route(state), "stop")

    def test_budget_stop_and_unread_read(self):
        state = agent.initial_state("降低RAG幻觉")
        state["api_calls"] = 8
        self.assertEqual(agent.route(state), "stop")
        state = agent.initial_state("降低RAG幻觉")
        state.update({"papers": [paper(1).model_dump()], "need_evidence": True})
        self.assertEqual(agent.route(state), "read")


class AgentGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        # 覆盖度评估不可用时按规则回退，保证测试离线、确定性
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_graph_uses_existing_search_tool_and_stops_on_limit(self):
        limited = {
            "papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"},
            "source_retry_after": {"arxiv": 60},
        }
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned("rag hallucination"))), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=limited)):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["stop_reason"])
        self.assertIn("限流", result["stop_reason"])
        self.assertEqual(result["api_calls"], 1)
        self.assertTrue(any(event["step"] == "search" for event in result["events"]))
        self.assertTrue(any(event["step"] == "stop" for event in result["events"]))


class AgentHitlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    def _planned(self):
        return {"queries": ["q1", "q2"], "exclusions": [], "rationale": "按主题拆分检索"}

    def _empty_search(self):
        return {"papers": [], "source_errors": {}, "source_retry_after": {}}

    async def test_plan_interrupt_payload_and_edit_queries_on_resume(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            first = await agent.run("如何降低RAG幻觉", task_id="hitl-plan")
        self.assertEqual(first["status"], "waiting")
        payload = first["interrupt"]
        self.assertEqual(payload["type"], "review_plan")
        self.assertEqual(payload["goal"], "如何降低RAG幻觉")
        self.assertEqual(payload["plan"], [{"action": "search", "query": "q1"}, {"action": "search", "query": "q2"}])
        self.assertEqual(payload["queries"], ["q1", "q2"])
        self.assertEqual(payload["constraints"], {"year_from": None, "year_to": None})
        self.assertEqual(payload["budget"]["max_api_calls"], agent.MAX_API)
        self.assertEqual(payload["budget"]["max_llm_calls"], agent.MAX_LLM)
        self.assertEqual(payload["budget"]["max_extra_search"], agent.MAX_EXTRA_SEARCH)
        self.assertEqual(payload["budget"]["max_deep_reads"], agent.MAX_DEEP)

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            second = await agent.resume_task("hitl-plan", {"approved": True, "queries": ["edited query"]})
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["queries"], ["edited query"])
        self.assertEqual(second["llm_calls"], 2)  # 路由失败尝试 1 + 计划 1（恢复命中缓存不再计费）
        self.assertEqual(sum(1 for event in second["events"] if event["step"] == "plan"), 1)
        self.assertEqual(second["plan_version"], 1)

    async def test_plan_resume_ignores_malformed_fields(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            first = await agent.run("如何降低RAG幻觉", task_id="hitl-malformed")
        self.assertEqual(first["status"], "waiting")
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            second = await agent.resume_task("hitl-malformed", {"approved": True, "queries": "not-a-list", "plan": {"bad": 1}})
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["queries"], ["q1", "q2"])
        self.assertEqual(second["plan"], [{"action": "search", "query": "q1"}, {"action": "search", "query": "q2"}])

    async def test_plan_rejected_on_resume_stops_as_user_cancel(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            first = await agent.run("如何降低RAG幻觉", task_id="hitl-reject")
        self.assertEqual(first["status"], "waiting")
        # 恢复时拒绝计划：不再发起检索，直接以“用户取消”结束
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())):
            second = await agent.resume_task("hitl-reject", {"approved": False})
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["stop_reason"], "用户取消")

    async def test_auto_approve_skips_interrupt_and_completes(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="hitl-auto")
        self.assertEqual(result["status"], "done")
        self.assertIsNone(result["interrupt"])
        self.assertEqual(sum(1 for event in result["events"] if event["step"] == "plan"), 1)
        self.assertTrue(result["stop_reason"])

    async def test_cancel_before_run_stops_with_user_cancel(self):
        agent.request_cancel("hitl-cancel-early")
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="hitl-cancel-early")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "用户取消")
        self.assertFalse(agent.is_cancel_requested("hitl-cancel-early"))

    async def test_cancel_during_run_stops_with_user_cancel(self):
        def cancel_mid_run(*args, **kwargs):
            agent.request_cancel("hitl-cancel-mid")
            return self._empty_search()

        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(side_effect=cancel_mid_run)):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="hitl-cancel-mid")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "用户取消")

    async def test_pause_then_resume_completes(self):
        agent.request_pause("hitl-pause")
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=self._planned())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            paused = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="hitl-pause")
        self.assertEqual(paused["status"], "waiting")
        self.assertEqual(paused["interrupt"]["type"], "paused")
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=self._empty_search())):
            resumed = await agent.resume_task("hitl-pause", True)
        self.assertEqual(resumed["status"], "done")
        self.assertIsNone(agent._flag_of("hitl-pause"))


class AgentToolTests(unittest.IsolatedAsyncioTestCase):
    """业务工具单元测试：缓存、计费、证据绑定。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_search_tool_cache_hit_does_not_rebill(self):
        search_mock = AsyncMock(return_value=found(3))
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            first = await agent._tool_search_papers(agent.initial_state("goal"), {"query": "q1"})
            second = await agent._tool_search_papers(agent.initial_state("goal"), {"query": "q1"})
        self.assertEqual(search_mock.call_count, 1)
        self.assertEqual(first[1]["api_calls"], 1)
        self.assertEqual(second[1]["api_calls"], 0)
        self.assertTrue(second[0].find('"cached": true') != -1)

    async def test_search_tool_error_responses_are_not_cached(self):
        limited = {"papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流）"}, "source_retry_after": {"arxiv": 60}}
        search_mock = AsyncMock(return_value=limited)
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            await agent._tool_search_papers(agent.initial_state("goal"), {"query": "q1"})
            await agent._tool_search_papers(agent.initial_state("goal"), {"query": "q1"})
        self.assertEqual(search_mock.call_count, 2)

    async def test_search_tool_appends_query_and_counts_extra(self):
        search_mock = AsyncMock(return_value=found(2))
        state = agent.initial_state("goal")
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            _, first = await agent._tool_search_papers(state, {"query": "q1"})
            state2 = {**agent.initial_state("goal"), **first}
            _, second = await agent._tool_search_papers(state2, {"query": "q2", "page": 1})
        self.assertEqual(second["queries"], ["q1", "q2"])
        self.assertEqual(second["extra_search"], 1)  # 第二次检索计为补检索

    async def test_search_tool_enforces_year_constraints(self):
        search_mock = AsyncMock(return_value=found(2))
        state = {**agent.initial_state("goal"), "year_from": 2020, "year_to": 2024}
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            await agent._tool_search_papers(state, {"query": "RAG hallucination 2019 survey"})
        args = search_mock.call_args[0]
        self.assertEqual(args[5], 2020)
        self.assertEqual(args[6], 2024)
        self.assertNotIn("2019", args[0])

    async def test_assess_tool_cache_hit_avoids_llm_call(self):
        llm_mock = AsyncMock(side_effect=[json.dumps({"coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "r", "new_query": None})])
        state = {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]}
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            _, first = await agent._tool_assess_coverage(state, {})
            _, second = await agent._tool_assess_coverage(state, {})
        self.assertEqual(llm_mock.call_count, 1)
        self.assertEqual(first["llm_calls"], 1)
        self.assertEqual(second["llm_calls"], 0)

    async def test_assess_tool_failure_falls_back_to_rules_not_cached(self):
        llm_mock = AsyncMock(side_effect=llm.AnalysisError("模型调用失败"))
        state = {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]}
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            content1, first = await agent._tool_assess_coverage(state, {})
            content2, second = await agent._tool_assess_coverage(state, {})
        self.assertEqual(llm_mock.call_count, 2)
        self.assertIn("fallback", content1)
        self.assertEqual(first["coverage"]["next_action"], "read_next")  # 规则回退：未读且未深读
        self.assertEqual(second["llm_calls"], 0)

    async def test_retrieve_tool_binds_evidence_ids(self):
        chunk = {"text": "Retrieval reduces hallucination with citations.", "chunk_id": "c1", "section": "intro", "paragraph_ids": ["p1"]}
        with patch("app.services.retrieval.hybrid.retrieve", new=AsyncMock(return_value=[chunk])):
            content, updates = await agent._tool_retrieve_paper_evidence(
                {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]},
                {"paper_id": "arxiv:1", "aspect": "hallucination"},
            )
        self.assertEqual(updates["evidence_papers"].get("E0001"), "arxiv:1")
        self.assertIn("E0001", updates["evidence_ids"])
        self.assertIn("E0001", content)

    async def test_analyze_tool_binds_report_evidence(self):
        report = {
            "llm_calls": 1, "status": "completed",
            "papers": [{
                "paper_id": "arxiv:1", "title": "Paper 1", "status": "completed",
                "fields": [{"label": "研究内容", "status": "found", "content": "c", "evidence": [{"quote": "Retrieval reduces hallucination in generation tasks.", "section": "abs"}], "missing": []}],
            }],
        }
        with patch("app.tools.analyze_paper.analyze_paper", new=AsyncMock(return_value=report)):
            content, updates = await agent._tool_analyze_paper(
                {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]},
                {"paper_ids": ["arxiv:1"]},
            )
        self.assertEqual(updates["llm_calls"], 1)
        self.assertEqual(updates["evidence_papers"].get("E0001"), "arxiv:1")
        self.assertIn("E0001", updates["evidence_ids"])
        self.assertIn("new_evidence", content)


class AgentJudgeTests(unittest.IsolatedAsyncioTestCase):
    """Judge 四维检查单元测试（直接驱动 claims/judge/revise 节点）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def _state(self, answer="结论A。", goal="如何降低RAG幻觉", **extra):
        state = {
            **agent.initial_state(goal),
            "answer": answer,
            "evidence_pool": {"E0001": "quote a"},
            "evidence_papers": {"E0001": "arxiv:1"},
            "stop_reason": "目标覆盖完成",
            **extra,
        }
        return state

    async def test_all_supported_no_revision(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ])):
            state = self._state()
            after_claims = await agent.claims_node(state)
            after_judge = await agent.judge_node({**state, **after_claims})
        self.assertEqual(after_judge["review"]["rounds"], 1)
        self.assertTrue(after_judge["review"]["semantics_done"])
        self.assertEqual(after_judge["claims"][0]["status"], "supported")
        self.assertIsNone(after_judge["review"]["coverage_note"])
        self.assertIsNone(after_judge["review"]["multi_paper_note"])

    async def test_precheck_catches_fabricated_evidence_id_without_llm(self):
        llm_mock = AsyncMock(side_effect=[claims_json(("存在X效应。", ["EV-999"]))])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            state = self._state(answer="存在X效应。")
            after_claims = await agent.claims_node(state)
            after_judge = await agent.judge_node({**state, **after_claims})
        self.assertEqual(llm_mock.call_count, 1)  # judge LLM 未被调用
        rule_results = [item for item in after_judge["review"]["results"] if item["verdict"] == "rule_failed"]
        self.assertEqual(len(rule_results), 1)
        self.assertEqual(rule_results[0]["issue"], "证据ID无效")
        self.assertFalse(after_judge["review"]["semantics_done"])

    async def test_judge_llm_failure_keeps_rule_results(self):
        llm_mock = AsyncMock(side_effect=[claims_json(("结论A。", ["E0001"])), llm.AnalysisError("模型调用失败")])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            state = self._state()
            after_claims = await agent.claims_node(state)
            after_judge = await agent.judge_node({**state, **after_claims})
        self.assertFalse(after_judge["review"]["semantics_done"])
        self.assertTrue(any(item["verdict"] == "pass" for item in after_judge["review"]["results"]))
        self.assertEqual(after_judge["claims"][0]["status"], "pending")

    async def test_contradiction_triggers_revision_with_feedback_message(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("方法A全面优于方法B。", ["E0001"])),
            judge_json(verdict("c1", "contradiction", "证据与结论矛盾", "revise")),
        ])):
            state = {**self._state(answer="方法A全面优于方法B。"), "evidence_pool": {"E0001": "方法A在部分指标上更优。"}}
            after_claims = await agent.claims_node(state)
            merged = {**state, **after_claims}
            after_judge = await agent.judge_node(merged)
        self.assertEqual(after_judge["review"]["rounds"], 1)
        self.assertEqual(agent.judge_route(after_judge), "revise")
        with patch("app.infrastructure.llm.complete", new=AsyncMock()):
            after_revise = await agent.revise_node({**merged, **after_judge})
        feedback = after_revise["messages"][0]["content"]
        self.assertIn("复核反馈", feedback)
        self.assertIn("方法A全面优于方法B", feedback)

    async def test_bounded_two_rounds_then_finalize(self):
        # 第一轮：contradiction → 反馈；第二轮重新提取主张后仍 contradiction → finalize
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1", "contradiction", "证据矛盾", "revise")),
        ])):
            state = self._state()
            after_claims = await agent.claims_node(state)
            merged = {**state, **after_claims}
            round1 = await agent.judge_node(merged)
        self.assertEqual(agent.judge_route({**merged, **round1}), "revise")
        with patch("app.infrastructure.llm.complete", new=AsyncMock()):
            after_revise = await agent.revise_node({**merged, **round1})
        # 第二轮：新的答案/主张，judge 仍失败 → rounds=2 → finalize
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A（修正）。", ["E0001"])),
            judge_json(verdict("c1", "contradiction", "仍然矛盾", "revise")),
        ])):
            merged2 = {**merged, **after_revise, "answer": "结论A（修正）。"}
            after_claims2 = await agent.claims_node(merged2)
            round2 = await agent.judge_node({**merged2, **after_claims2})
        self.assertEqual(round2["review"]["rounds"], 2)
        self.assertEqual(agent.judge_route({**merged2, **round2}), "finalize")

    async def test_coverage_note_flags_incomplete_goal(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ])):
            state = self._state(coverage={"coverage": "insufficient", "missing": ["实验证据"], "next_action": "search_more", "rationale": "r", "new_query": None})
            after_claims = await agent.claims_node(state)
            after_judge = await agent.judge_node({**state, **after_claims})
        self.assertIn("任务完成度", after_judge["review"]["coverage_note"])
        self.assertEqual(agent.judge_route(after_judge), "revise")

    async def test_multi_paper_note_only_for_comparative_goals(self):
        comparative_goal = "对比方法A与方法B的优劣"
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A。", ["E0001"])), judge_json(verdict("c1")),
        ])):
            comparative_state = self._state(goal=comparative_goal)
            comparative = await agent.judge_node({**comparative_state, **(await agent.claims_node(comparative_state))})
        self.assertIn("多篇覆盖", comparative["review"]["multi_paper_note"] or "")
        single_goal = "arxiv:1234.5678 提出了什么方法"
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            claims_json(("结论A。", ["E0001"])), judge_json(verdict("c1")),
        ])):
            single_state = self._state(goal=single_goal)
            single = await agent.judge_node({**single_state, **(await agent.claims_node(single_state))})
        self.assertIsNone(single["review"]["multi_paper_note"])

    async def test_finalize_downgrades_remaining_failures(self):
        state = self._state(answer="结论A。结论B。")
        state["claims"] = [
            {"claim_id": "c1", "text": "结论A。", "evidence_ids": ["E0001"], "status": "supported"},
            {"claim_id": "c2", "text": "结论B。", "evidence_ids": ["E0002"], "status": "pending"},
        ]
        state["review"] = {"rounds": 2, "results": [], "to_verify": [], "semantics_done": True, "fixed_claims": [], "downgraded_claims": []}
        finalized = agent.finalize_answer_node(state)
        self.assertIn("待核实", finalized["answer"])
        self.assertEqual(finalized["review"]["to_verify"], ["结论B。"])
        self.assertEqual(finalized["review"]["downgraded_claims"], ["c2"])


class AgentReActLoopTests(unittest.IsolatedAsyncioTestCase):
    """ReAct 循环：多工具单轮、无工具调用进 Judge、迭代上限。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_tool_node_executes_multiple_calls_in_order(self):
        state = {
            **agent.initial_state("goal"),
            "papers": [paper(1).model_dump()],
            "messages": [
                {"role": "assistant", "content": "{}", "tool_calls": [
                    {"id": "call_1_0", "name": "search_papers", "args": {"query": "q1"}, "type": "tool_call"},
                    {"id": "call_1_1", "name": "retrieve_paper_evidence", "args": {"paper_id": "arxiv:1"}, "type": "tool_call"},
                ]},
            ],
        }
        chunk = {"text": "Retrieval reduces hallucination with citations.", "chunk_id": "c1", "section": "s", "paragraph_ids": ["p1"]}
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=found(3))), \
             patch("app.services.retrieval.hybrid.retrieve", new=AsyncMock(return_value=[chunk])):
            result = await agent.tool_node(state)
        tool_msgs = [m for m in result["messages"]]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1_0")
        self.assertEqual(tool_msgs[1]["tool_call_id"], "call_1_1")
        self.assertEqual(tool_msgs[0]["name"], "search_papers")
        self.assertEqual(tool_msgs[1]["name"], "retrieve_paper_evidence")
        self.assertEqual(len(result["papers"]), 3)
        self.assertIn("E0001", result["evidence_ids"])
        tool_events = [e for e in result["events"] if e["step"] in ("search", "tool")]
        self.assertEqual(len(tool_events), 2)

    async def test_no_tool_calls_routes_to_judge(self):
        state = {
            **agent.initial_state("goal"),
            "stop_reason": "目标覆盖完成",
            "answer": "结论A。",
            "evidence_pool": {"E0001": "原文材料"},
            "messages": [{"role": "assistant", "content": rj(answer="结论A。")}],
            "react_turns": 1,
        }
        self.assertEqual(agent.react_route(state), "done")
        # 新契约：空证据主张被规则预检拒绝（见验收 test_judge_cannot_verify_scientific_claim_without_evidence），
        # 这里主张须带池内证据ID 才能进入语义复核并收尾。
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[claims_json(("结论A。", ["E0001"])), judge_json(verdict("c1"))])):
            after_claims = await agent.claims_node(state)
            after_judge = await agent.judge_node({**state, **after_claims})
        self.assertEqual(after_judge["review"]["rounds"], 1)
        self.assertEqual(agent.judge_route(after_judge), "finalize")

    async def test_react_iteration_cap_stops_loop(self):
        compiled = await agent.graph()
        state = agent.initial_state("如何降低RAG幻觉", auto_approve=True)
        state["max_llm_calls"] = 200
        state["max_api_calls"] = 200
        state["max_extra_search"] = 200
        always_tools = rj(("search_papers", {"query": "q"}))
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned("q1"))), \
             patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=always_tools)), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=found(1))):
            result = await compiled.ainvoke(state, {"configurable": {"thread_id": "cap-test"}})
        self.assertLessEqual(result["react_turns"], agent.MAX_REACT)
        self.assertEqual(result["stop_reason"], "达到最大迭代次数")


class AgentSqliteCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "sqlite",
        }, clear=False)
        self.env.start()
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        await agent.reset_graph()

    async def asyncTearDown(self):
        await agent.reset_graph()
        await asyncio.sleep(0.05)  # 让 aiosqlite 后台线程落定，避免 Windows 下偶发访问冲突
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_async_sqlite_checkpointer_records_thread(self):
        limited = {
            "papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"},
            "source_retry_after": {"arxiv": 60},
        }
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned("rag hallucination"))), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=limited)):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True)
        self.assertEqual(result["status"], "done")
        self.assertTrue(agent.checkpoint_path().exists())
        self.assertGreater(agent.checkpoint_path().stat().st_size, 0)

    async def test_paused_task_survives_restart_and_resumes(self):
        planned_state = planned("q1")
        empty = {"papers": [], "source_errors": {}, "source_retry_after": {}}
        agent.request_pause("sqlite-pause")
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_state)), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=empty)):
            paused = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="sqlite-pause")
        self.assertEqual(paused["status"], "waiting")

        # 模拟进程重启：内存图与检查点连接全部重建，仅保留 sqlite 文件
        await agent.reset_graph()
        agent._GRAPH = None
        agent._CHECKPOINTER = None
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=empty)):
            resumed = await agent.resume_task("sqlite-pause", True)
        self.assertEqual(resumed["status"], "done")
        self.assertTrue(resumed["stop_reason"])
