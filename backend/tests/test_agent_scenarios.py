"""任务书十二项 Agent 专项用例 + SSE 事件流测试。

每个场景端到端跑通图（mock 检索/LLM/全文/分析），断言停止条件、
状态一致性与无重复计费；call_cache 通过独立临时库自动隔离。
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.agents import agent
from app.infrastructure import llm
from app.main import app
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


def found(n, start=1):
    papers = [paper(i, abstract=f"abstract {i} about RAG hallucination mitigation.").model_dump() for i in range(start, start + n)]
    return {"papers": papers, "source_errors": {}, "source_retry_after": {}}


EMPTY_SEARCH = {"papers": [], "source_errors": {}, "source_retry_after": {}}


def planned(*queries):
    return {"queries": list(queries), "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}


def assess_json(next_action, rationale="r", coverage="sufficient", missing=None, new_query=None):
    return json.dumps({"coverage": coverage, "missing": missing or [], "next_action": next_action, "rationale": rationale, "new_query": new_query})


def route_plan():
    """router_node 消耗的首个 LLM 调用：路由决策（plan），让既有场景保持计划流。"""
    return json.dumps({"mode": "plan", "rationale": "调研性质，需要规划"})


def assess_synthesize():
    return assess_json("synthesize", "证据充分")


def claims_json(*claims):
    return json.dumps({"claims": [{"text": text, "evidence_ids": eids} for text, eids in claims]})


def judge_json(*reviews):
    return json.dumps({"reviews": list(reviews)})


def verdict(claim_id, outcome="supported", issue=None, action="keep"):
    return {"claim_id": claim_id, "verdict": outcome, "issue": issue, "action": action, "quote": ""}


def answer_result(answer, *evidence):
    return {"answer": answer, "evidence": [{"evidence_id": eid, "quote": quote} for eid, quote in evidence], "reviews": []}


def manager(target, mock):
    return patch(target, new=mock)


def start_managers(managers):
    return [m.start() for m in managers]


def stop_managers(managers):
    for m in reversed(managers):
        m.stop()


class ScenarioTestBase(unittest.IsolatedAsyncioTestCase):
    checkpoint = "memory"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": self.checkpoint,
        })
        self.env.start()
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        await asyncio.sleep(0.05)  # 让 aiosqlite 后台线程落定，避免 Windows 下偶发访问冲突
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    def run_managers(self, task_id, llm_side_effect, search=None, answer=None, extra=None):
        search = search if search is not None else AsyncMock(return_value=found(5))
        managers = [
            manager("app.services.analysis.attribution.decompose", AsyncMock(return_value=planned("q1"))),
            manager("app.infrastructure.llm.complete", AsyncMock(side_effect=llm_side_effect)),
            manager("app.services.retrieval.search.search_papers", search),
            manager("app.services.analysis.attribution.answer_question", AsyncMock(return_value=answer or answer_result(
                "结论A。结论B。结论C。", ("E0001", "quote a"), ("E0002", "quote b"), ("E0003", "quote c"),
            ))),
        ]
        for target, mock in (extra or {}).items():
            managers.append(manager(target, mock))
        return managers

    async def run_scenario(self, task_id, llm_side_effect, **kwargs):
        managers = self.run_managers(task_id, llm_side_effect, **kwargs)
        start_managers(managers)
        try:
            return await agent.run("如何降低RAG幻觉", auto_approve=True, task_id=task_id)
        finally:
            stop_managers(managers)


class AgentScenarioTests(ScenarioTestBase):
    async def test_01_normal_completion(self):
        """场景1 正常完成：plan→search→assess sufficient→synthesize→judge 通过→done。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"]), ("结论B。", ["E0002"]), ("结论C。", ["E0003"])),
            judge_json(verdict("c1"), verdict("c2"), verdict("c3")),
        ]
        result = await self.run_scenario("sc-01", side)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "目标覆盖完成")
        self.assertEqual(result["answer"], "结论A。结论B。结论C。")  # judge 全通过，答案未改动
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["llm_calls"], 5)  # assess + synthesize + claims + judge
        self.assertEqual(result["extra_search"], 0)
        self.assertEqual(result["assessments"], 1)
        self.assertEqual(result["judge_round"], 1)
        self.assertEqual(result["review"]["to_verify"], [])

    async def test_02_vague_terms_extend_query(self):
        """场景2 术语含糊：评估给出新的扩展检索式，queries 增长并记录理由。"""
        new_query = "self-consistency verifier grounded citation RAG"
        side = [
            route_plan(),
            assess_json("search_more", "候选太少且相关性低", "insufficient", ["相关方法"], new_query),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"]), ("结论B。", ["E0002"]), ("结论C。", ["E0003"])),
            judge_json(verdict("c1"), verdict("c2"), verdict("c3")),
        ]
        managers = self.run_managers("sc-02", side)
        search = managers[2].new
        start_managers(managers)
        try:
            result = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="sc-02")
        finally:
            stop_managers(managers)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(result["queries"]), 2)
        self.assertEqual(result["queries"][1], new_query)
        self.assertEqual(search.call_args_list[1][0][0], new_query)  # 第二次检索确实用了新检索式
        extend = next(e for e in result["events"] if e["step"] == "extend_terms")
        self.assertEqual(extend["query"], new_query)
        assess = next(e for e in result["events"] if e["step"] == "assess")
        self.assertIn("相关性低", assess["reason"])

    async def test_03_no_candidates(self):
        """场景3 无候选：两源均无结果 → assess stop → finish，stop_reason 指明无候选，未尝试精读。"""
        side = [route_plan(), assess_json("stop", "没有检索到候选", "insufficient", ["任何候选"])]
        result = await self.run_scenario("sc-03", side, search=AsyncMock(return_value=dict(EMPTY_SEARCH)))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "没有检索到候选")
        self.assertEqual(result["deep_reads"], 0)
        self.assertFalse(any(e["step"] == "read" for e in result["events"]))
        self.assertEqual(result["paper_ids"], [])

    async def test_04_insufficient_coverage_paginates(self):
        """场景4 覆盖不足：首次评估 insufficient → 翻页补检索 → 二次检索合并候选 → synthesize。"""
        side = [
            route_plan(),
            assess_json("paginate", "第一页结果少", "insufficient", ["更多候选"]),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"]), ("结论B。", ["E0002"]), ("结论C。", ["E0003"])),
            judge_json(verdict("c1"), verdict("c2"), verdict("c3")),
        ]
        search = AsyncMock(side_effect=[found(2), found(5)])
        result = await self.run_scenario("sc-04", side, search=search)
        self.assertEqual(result["status"], "done")
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[1][0][1], 2)  # 第二页
        self.assertEqual(len(result["paper_ids"]), 5)  # 候选合并去重
        self.assertEqual(result["extra_search"], 1)
        self.assertEqual(len([e for e in result["events"] if e["step"] == "search"]), 2)
        self.assertEqual(result["stop_reason"], "目标覆盖完成")

    async def test_05_fulltext_unavailable_records_gap(self):
        """场景5 全文不可得：prepare 全部失败 → gaps 记录“全文不可得:*”，不停滞，最终状态保留缺口。"""
        side = [
            route_plan(),
            assess_json("read_next", "候选足够但缺全文证据", "partial", ["实验证据"]),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ]
        answer = answer_result("结论A。", ("E0001", "quote a"))
        extra = {
            "app.services.papers.library.save_paper": MagicMock(return_value=(paper(1, abstract="abstract 1 about RAG hallucination mitigation."), False)),
            "app.services.analysis.fulltext.prepare": AsyncMock(side_effect=Exception("全文下载失败")),
            "app.services.retrieval.hybrid.index_prepared": MagicMock(),
            "app.tools.analyze_paper.analyze_paper": AsyncMock(return_value={"llm_calls": 1, "papers": [{"status": "ok", "evidence_ids": []}]}),
        }
        result = await self.run_scenario("sc-05", side, answer=answer, extra=extra)
        self.assertEqual(result["status"], "done")
        self.assertTrue(any(gap.startswith("全文不可得:arxiv:1") for gap in result["gaps"]))
        self.assertEqual(result["deep_reads"], 1)  # 尝试过精读但不重复
        self.assertEqual(result["answer"], "结论A。")
        self.assertFalse(any(e["step"] == "read" and "analyze_paper" in e.get("tool", "") for e in result["events"]))

    async def test_06_single_source_rate_limit_partial_results(self):
        """场景6 单源限流：一个来源 429，另一个成功 → 保留部分结果，source_errors 记录，事件注明原因。"""
        partial = {
            "papers": [paper(1, abstract="abstract 1 about RAG hallucination mitigation.").model_dump()],
            "source_errors": {"semantic_scholar": "Semantic Scholar 上游返回 429（限流），约 60 秒后可重试"},
            "source_retry_after": {"semantic_scholar": 60},
        }
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ]
        answer = answer_result("结论A。", ("E0001", "quote a"))
        result = await self.run_scenario("sc-06", side, search=AsyncMock(return_value=partial), answer=answer)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["paper_ids"], ["arxiv:1"])
        search_event = next(e for e in result["events"] if e["step"] == "search")
        self.assertEqual(search_event["reason"], "来源部分失败，保留已取得结果")
        self.assertIn("semantic_scholar", search_event["errors"])
        self.assertEqual(result["stop_reason"], "来源限流，返回部分结果")
        self.assertEqual(result["answer"], "结论A。")

    async def test_07_unclear_figure_degrades_honestly(self):
        """场景7 图像不清：无多模态节点（F07），图表结论无证据支撑时被程序化预检拦下并标记待核实，而不是放行杜撰数字。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("图3显示引入检索后准确率提升12%。", ["E0001"])),
            # 预检（数字无出处）在 judge 之前拦截，不应再消耗评审调用
        ]
        answer = answer_result("图3显示引入检索后准确率提升12%。", ("E0001", "检索增强降低了幻觉率，未给出量化图表结论。"))
        result = await self.run_scenario("sc-07", side, answer=answer)
        self.assertEqual(result["status"], "done")
        review = result["review"]
        self.assertTrue(any(item["issue"] == "数字无出处" for item in review["results"]))
        self.assertFalse(review["semantics_done"])  # 未进入语义评审
        self.assertEqual(review["downgraded_claims"], ["c1"])
        self.assertEqual(review["to_verify"], ["图3显示引入检索后准确率提升12%。"])
        self.assertIn("待核实", result["answer"])  # 图表数字只能以待核实形式存在
        self.assertIn("复核说明", result["answer"])

    async def test_08_invalid_citation_caught_by_precheck(self):
        """场景8 引用无效：答案引用池外证据 ID → 程序化预检标记“证据ID无效”，主张被移除并留痕。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论X。", ["EV-999"])),
        ]
        answer = answer_result("结论X。", ("E0001", "quote a"))
        result = await self.run_scenario("sc-08", side, answer=answer)
        self.assertEqual(result["status"], "done")
        review = result["review"]
        self.assertTrue(any(item["verdict"] == "rule_failed" and item["issue"] == "证据ID无效" for item in review["results"]))
        self.assertEqual(review["downgraded_claims"], ["c1"])
        self.assertIn("已移除", result["answer"])
        self.assertIn("复核说明", result["answer"])
        self.assertEqual(result["llm_calls"], 4)  # assess + synthesize + claims，无 judge/revise

    async def test_09_contradiction_bounded_revision_then_downgrade(self):
        """场景9 证据冲突：judge 判 contradiction → 定向修订一轮；复判仍冲突 → 保守降级并列入待核实。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("方法A全面优于方法B。", ["E0001", "E0002"])),
            judge_json(verdict("c1", "contradiction", "证据与结论矛盾", "revise")),
            json.dumps({"revisions": [{"claim_id": "c1", "text": "方法A在部分指标上优于方法B。", "evidence_ids": ["E0001"]}]}),
            judge_json(verdict("c1", "contradiction", "改写后仍与证据矛盾", "revise")),
        ]
        answer = answer_result("方法A全面优于方法B。", ("E0001", "方法A在检索 recall 上更优。"), ("E0002", "方法B在生成准确率上更优。"))
        result = await self.run_scenario("sc-09", side, answer=answer)
        self.assertEqual(result["status"], "done")
        review = result["review"]
        self.assertEqual(review["rounds"], 2)  # 1 次初评 + 1 次复判，修订只有一轮
        self.assertEqual(len([e for e in result["events"] if e["step"] == "revise"]), 1)
        self.assertEqual(review["fixed_claims"], ["c1"])
        self.assertEqual(review["to_verify"], ["方法A在部分指标上优于方法B。"])
        self.assertEqual(review["downgraded_claims"], ["c1"])
        self.assertIn("待核实", result["answer"])
        self.assertNotIn("全面优于", result["answer"])  # 过于绝对的表述已被保守改写
        self.assertEqual(result["llm_calls"], 7)  # assess + synth + claims + judge + revise + judge

    async def test_10_api_budget_exhausted_stops_second_search(self):
        """场景10 预算耗尽：max_api_calls=1 而流程需要两次检索 → 第二次检索不发生，停止原因指明预算，返回部分状态。"""
        compiled = await agent.graph()
        state = agent.initial_state("如何降低RAG幻觉", auto_approve=True)
        state["max_api_calls"] = 1
        search = AsyncMock(return_value=found(3))
        side = [route_plan(), assess_json("search_more", "需要补充检索", "insufficient", ["更多候选"], "backup query")]
        managers = [
            manager("app.services.analysis.attribution.decompose", AsyncMock(return_value=planned("q1"))),
            manager("app.infrastructure.llm.complete", AsyncMock(side_effect=side)),
            manager("app.services.retrieval.search.search_papers", search),
        ]
        start_managers(managers)
        try:
            result = await compiled.ainvoke(state, {"configurable": {"thread_id": "sc-10"}})
        finally:
            stop_managers(managers)
        self.assertEqual(search.call_count, 1)  # 预算只允许一次检索
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["stop_reason"], "学术接口次数用尽")
        self.assertEqual(len(result["papers"]), 3)  # 部分状态仍返回
        self.assertEqual(result["extra_search"], 0)  # 预算在 assess 处拦截，extend 未执行

    async def test_11_user_cancel_mid_run(self):
        """场景11 用户取消：检索期间请求取消 → stop_reason“用户取消”，在飞工作不重复。"""
        def cancel_during_search(*args, **kwargs):
            agent.request_cancel("sc-11")
            return found(5)

        side = [route_plan()]  # 路由在取消发生前执行一次；之后 assess 在预算检查处停止，绝不调用 LLM
        result = await self.run_scenario("sc-11", side, search=AsyncMock(side_effect=cancel_during_search))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "用户取消")
        self.assertEqual(result["api_calls"], 1)  # 检索没有重发
        self.assertFalse(any(e["step"] == "read" for e in result["events"]))
        self.assertFalse(agent.is_cancel_requested("sc-11"))  # 标记已被 finish 清理


class AgentScenarioRecoveryTests(ScenarioTestBase):
    """场景12 中断恢复：暂停 → 重启（reset_graph）→ 恢复 API → 恢复执行，无重复计费。"""
    checkpoint = "sqlite"

    async def test_12_pause_restart_recover_resume_without_rebilling(self):
        def pause_during_search(*args, **kwargs):
            agent.request_pause("sc-12")
            return found(5)

        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ]
        answer = answer_result("结论A。", ("E0001", "quote a"))
        managers = self.run_managers("sc-12", side, search=AsyncMock(side_effect=pause_during_search), answer=answer)
        llm_mock = managers[1].new
        search_mock = managers[2].new
        start_managers(managers)
        try:
            paused = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="sc-12")
        finally:
            stop_managers(managers)
        self.assertEqual(paused["status"], "waiting")
        self.assertEqual(paused["interrupt"]["type"], "paused")
        self.assertEqual(llm_mock.call_count, 4)  # 路由 + assess + claims + judge 均已在暂停前完成
        self.assertEqual(search_mock.call_count, 1)
        self.assertEqual(paused["api_calls"], 1)
        self.assertEqual(paused["llm_calls"], 5)  # 路由+assess+synth+claims+judge；预检不计

        # 模拟进程重启：只保留 sqlite 文件，内存图与连接全部重建
        await agent.reset_graph()
        agent._GRAPH = None
        agent._CHECKPOINTER = None
        listed = agent.list_tasks()
        row = next(item for item in listed if item["task_id"] == "sc-12")
        self.assertEqual(row["status"], "waiting")
        self.assertEqual(row["interrupt"], "paused")

        recovered = await agent.recover_state("sc-12")
        self.assertEqual(recovered["status"], "waiting")
        self.assertEqual(recovered["interrupt"]["type"], "paused")

        resumed = await agent.resume_task("sc-12", True)
        self.assertEqual(resumed["status"], "done")
        self.assertEqual(resumed["stop_reason"], "目标覆盖完成")
        self.assertTrue(any(e["step"] == "recover" for e in resumed["events"]))
        # 恢复后没有重复计费：LLM 与检索调用次数与暂停前完全一致
        self.assertEqual(llm_mock.call_count, 4)
        self.assertEqual(search_mock.call_count, 1)
        self.assertEqual(resumed["llm_calls"], paused["llm_calls"])
        self.assertEqual(resumed["api_calls"], paused["api_calls"])


class ResearchEventStreamTests(ScenarioTestBase):
    async def test_stream_run_emits_node_progress_and_final_state(self):
        """stream_run：逐节点进度（精简负载）+ 最终 public_state。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ]
        answer = answer_result("结论A。", ("E0001", "quote a"))
        managers = self.run_managers("sse-live", side, answer=answer)
        start_managers(managers)
        try:
            items = [item async for item in agent.stream_run("如何降低RAG幻觉", auto_approve=True, task_id="sse-live")]
        finally:
            stop_managers(managers)
        nodes = [item["node"] for item in items if item["type"] == "node"]
        self.assertIn("plan", nodes)
        self.assertIn("search", nodes)
        self.assertIn("assess", nodes)
        self.assertIn("synthesize", nodes)
        self.assertIn("judge", nodes)
        search_item = next(item for item in items if item["type"] == "node" and item["node"] == "search")
        self.assertTrue(all(isinstance(pid, str) for pid in search_item["update"]["papers"]))  # 论文只留 ID
        self.assertNotIn("abstract", json.dumps(search_item["update"], ensure_ascii=False))
        final = items[-1]
        self.assertEqual(final["type"], "state")
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["task"]["stop_reason"], "目标覆盖完成")

    async def test_stream_run_waiting_emits_interrupt(self):
        """stream_run：计划审批挂起时产出 interrupt 事件并落盘 waiting。"""
        managers = [
            manager("app.services.analysis.attribution.decompose", AsyncMock(return_value=planned("q1"))),
        ]
        start_managers(managers)
        try:
            items = [item async for item in agent.stream_run("如何降低RAG幻觉", task_id="sse-wait")]
        finally:
            stop_managers(managers)
        final = items[-1]
        self.assertEqual(final["type"], "interrupt")
        self.assertEqual(final["interrupt"]["type"], "review_plan")
        saved = agent.get_task("sse-wait")
        self.assertEqual(saved["status"], "waiting")

    async def test_events_endpoint_replays_finished_task(self):
        """SSE 端点：已完成任务回放事件序列并以 [DONE] 结束。"""
        side = [
            route_plan(),
            assess_synthesize(),
            claims_json(("结论A。", ["E0001"])),
            judge_json(verdict("c1")),
        ]
        answer = answer_result("结论A。", ("E0001", "quote a"))
        managers = self.run_managers("sse-replay", side, answer=answer)
        start_managers(managers)
        try:
            await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="sse-replay")
        finally:
            stop_managers(managers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/research/tasks/sse-replay/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        body = response.text
        self.assertIn('"step": "plan"', body)
        self.assertIn('"step": "search"', body)
        self.assertIn('"step": "stop"', body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    async def test_events_endpoint_emits_interrupt_for_waiting_task(self):
        """SSE 端点：挂起任务下发 interrupt 事件后结束（客户端走 resume 端点继续）。"""
        managers = [
            manager("app.services.analysis.attribution.decompose", AsyncMock(return_value=planned("q1"))),
        ]
        start_managers(managers)
        try:
            await agent.run("如何降低RAG幻觉", task_id="sse-interrupt")
        finally:
            stop_managers(managers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/research/tasks/sse-interrupt/events")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('"type": "interrupt"', body)
        self.assertIn("review_plan", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    async def test_events_endpoint_unknown_task_without_goal_404(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/research/tasks/sse-missing/events")
        self.assertEqual(response.status_code, 404)
