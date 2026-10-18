"""Behavioral tests for the evidence workflow, with real scope/storage and no network."""

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent, store, workflow
from app.agents.state import MAX_EVIDENCE_ITEMS, add_evidence, initial_state
from app.infrastructure import llm, rerank
from app.schemas import PaperOut
from app.services.analysis import fulltext
from app.services.papers import documents, library, workspaces


def public_paper(n=1, **kwargs):
    return PaperOut(id=f"arxiv:2401.{12000+n}", title=f"Paper {n}", year=2024,
                    abstract="Retrieval provides evidence for answering questions.", sources=["arxiv"], **kwargs)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "RESEARCH_CHECKPOINT": "memory", "LANGCHAIN_TRACING_V2": "false"})
        self.env.start()
        self.mocks = []
        self.mock("app.infrastructure.rerank.rerank", AsyncMock(side_effect=rerank.RerankError("offline")))
        self.model = self.mock("app.infrastructure.llm.complete", AsyncMock(side_effect=self.complete))
        self.search = self.mock("app.services.retrieval.search.search_papers", AsyncMock(return_value={
            "papers": [public_paper(2)], "source_errors": {}, "source_retry_after": {}}))
        self.mock("app.services.analysis.attribution.decompose", AsyncMock(return_value={
            "queries": ["retrieval evidence", "grounded answers"], "rationale": "研究计划"}))
        self.mock("app.infrastructure.embeddings.embed_siliconflow", AsyncMock(side_effect=AssertionError("unexpected embeddings")))
        workspaces.register("w", "Test")
        self.local, _ = library.save_paper(public_paper(), public_paper().id)
        workspaces.add("w", self.local.local_id)
        documents.save(self.local.local_id, self.local.id,
                       "The method uses retrieval to obtain evidence. The experiments compare grounded answers.", "test.pdf")
        self.context = {"workspace_id": "w", "selected_paper_ids": [self.local.id], "accessible_paper_ids": [self.local.id]}
        self.assessments = []
        self.quote_override = None
        self.synthesis_count = 0

    def mock(self, name, value):
        p = patch(name, new=value)
        p.start()
        self.mocks.append(p)
        return value

    async def asyncTearDown(self):
        await agent.reset_graph()
        for p in reversed(self.mocks):
            p.stop()
        self.env.stop()
        self.temp.cleanup()

    async def complete(self, messages):
        system = messages[0]["content"]
        data = messages[-1]["content"]
        if "任务路由员" in system:
            return json.dumps({"mode": "react", "research_intent": "auto", "domains": ["analysis"]})
        payload = json.loads(data)
        if "只复制原文" in system:
            quotes = [{"chunk_id": row["chunk_id"], "quote": row["quote"][:900]} for row in payload["passages"][:2]]
            return json.dumps({"quotes": quotes if self.quote_override is None else self.quote_override})
        if "覆盖度评估员" in system:
            return json.dumps(self.assessments.pop(0) if self.assessments else {
                "coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "材料充分"})
        if "你是论文研究助手" in system:
            self.synthesis_count += 1
            return json.dumps({"answer": "\n".join(f"材料提供了相关依据。[{row['evidence_id']}]" for row in payload["evidence"])})
        if "拆成可独立核验" in system:
            return json.dumps({"claims": [{"text": line, "evidence_ids": re.findall(r'E\d{4}', line)}
                                          for line in payload["answer"].splitlines()]})
        if "独立评审" in system:
            return json.dumps({"reviews": [{"claim_id": row["claim_id"], "verdict": "supported", "action": "keep",
                                            "quote": row["evidence"][0]["quote"][:30]} for row in payload["claims"]]})
        raise AssertionError(f"Unexpected model role: {system[:80]}")

    async def run_task(self, goal="分析这篇论文的实验", **kwargs):
        return await agent.run(goal, mode="react", auto_approve=True, context=kwargs.pop("context", self.context), **kwargs)

    async def test_local_details_force_read_and_quotes_without_model_tool_calls(self):
        result = await self.run_task()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["research_intent"], "read")
        self.search.assert_not_awaited()
        self.assertIn("full_text", {r["basis"] for r in result["reading_status"].values()})
        self.assertEqual(result["review"]["final_status"], "verified")
        self.assertTrue({"prepare", "read", "quotes", "assess", "synthesize", "judge"} <= {e["step"] for e in result["events"]})
        self.assertFalse(any(e["step"] == "tool" for e in result["events"]))
        self.assertFalse(any("重排服务" in gap or "没有已保存的文本块" in gap for gap in result["gaps"]))

    async def test_selected_paper_does_not_disable_new_search(self):
        result = await self.run_task("分析这篇论文，再搜索相关论文")
        self.assertEqual(result["research_intent"], "mixed")
        self.search.assert_awaited_once()
        self.assertIn(public_paper(2).id, result["discovered_paper_ids"])
        self.assertEqual(set(result["reading_status"]), {self.local.id, public_paper(2).id})
        self.assertEqual(result["accessible_paper_ids"], [self.local.id])
        self.assertEqual(len(workspaces.list_papers("w")["papers"]), 1)

    async def test_public_search_does_not_read_unselected_local_pdf(self):
        # The public result has the same ID as a private/unselected locally stored PDF.
        self.search.return_value["papers"] = [public_paper()]
        with patch("app.services.papers.documents.get_text", side_effect=AssertionError("private PDF accessed")):
            result = await self.run_task("搜索论文", context={"workspace_id": "w", "accessible_paper_ids": []})
        self.assertEqual(result["reading_status"][self.local.id]["basis"], "abstract")
        self.assertFalse(any("The method uses" in e["quote"] for e in result["evidence"]))

    async def test_forged_quotes_are_rejected_before_synthesis(self):
        self.quote_override = [{"chunk_id": f"W0:{self.local.id}", "quote": "Invented evidence does not appear in this paper."}]
        result = await self.run_task()
        self.assertEqual(result["evidence"], [])
        self.assertEqual(self.synthesis_count, 0)
        self.assertIn("不能确认", result["answer"])

    async def test_extraction_failure_keeps_actual_passages_and_warning(self):
        original = self.complete
        async def fail_extract(messages):
            if "只复制原文" in messages[0]["content"]:
                raise llm.AnalysisError("extract unavailable")
            return await original(messages)
        self.model.side_effect = fail_extract
        result = await self.run_task()
        self.assertTrue(result["evidence"])
        self.assertTrue(any("原文提取不可用" in gap for gap in result["gaps"]))

    async def test_read_only_scope_prevents_automatic_external_expansion(self):
        self.assessments = [{"coverage": "partial", "missing": ["跨领域实验"], "next_action": "search_more",
                             "new_query": "transfer learning", "rationale": "缺少材料"}]
        result = await self.run_task("仅根据这篇论文分析实验")
        self.search.assert_not_awaited()
        self.assertEqual(result["coverage"]["coverage"], "partial")

    async def test_gap_retrieval_executes_once_and_changes_page(self):
        self.assessments = [{"coverage": "partial", "missing": ["其他方法"], "next_action": "paginate", "rationale": "补充"}]
        result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.assertEqual(self.search.await_count, 2)
        self.assertEqual([call.args[1] for call in self.search.await_args_list], [1, 2])
        self.assertEqual(result["search_count"], 2)
        self.assertEqual(len(result["reading_status"]), 1)

    async def test_read_failure_does_not_destroy_other_paper_results(self):
        original = workflow._read
        async def read(state, pid):
            if pid == self.local.id:
                raise ValueError("bad PDF")
            return await original(state, pid)
        with patch("app.agents.workflow._read", new=read):
            result = await self.run_task("分析这篇论文，再搜索相关论文")
        self.assertTrue(result["evidence"])
        self.assertEqual(result["reading_status"][self.local.id]["status"], "insufficient")
        self.assertIn(public_paper(2).id, result["reading_status"])

    async def test_repeat_read_stage_is_cached_within_task(self):
        state = initial_state("分析论文", task_id="same", context=self.context)
        state.update(await workflow.prepare_node(state))
        with patch("app.agents.workflow._read", wraps=workflow._read) as read:
            first = await workflow.read_node(state)
            state.update(first)
            second = await workflow.read_node(state)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(first["workflow_rows"], second["workflow_rows"])

    async def test_only_transient_read_errors_are_retried(self):
        state = initial_state("q", task_id="transient")
        producer = AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), {"rows": []}])
        await workflow._call(state, "read", {}, producer, retry=True)
        self.assertEqual(producer.await_count, 2)
        producer = AsyncMock(side_effect=ValueError("invalid ID"))
        await workflow._call(state, "invalid", {}, producer, retry=True)
        self.assertEqual(producer.await_count, 1)

    async def test_plan_confirmation_resume_does_not_search_before_approval(self):
        task = await agent.run("分析这篇论文", context=self.context, mode="plan", task_id="approval")
        self.assertEqual(task["status"], "waiting")
        self.search.assert_not_awaited()
        result = await agent.resume_task("approval", {"approved": True})
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["evidence"])

    async def test_cancel_before_read_prevents_model_and_download(self):
        state = initial_state("q", task_id="cancelled", context=self.context)
        state.update(await workflow.prepare_node(state))
        store.set_flag("cancelled", "cancel")
        with patch("app.agents.workflow._read", new=AsyncMock()) as read:
            await workflow.read_node(state)
        read.assert_not_awaited()
        self.assertEqual(workflow.read_route(state), "finish")

    async def test_reading_many_papers_is_bounded_and_reports_gap(self):
        papers = []
        for n in range(10, 17):
            p, _ = library.save_paper(public_paper(n), public_paper(n).id)
            papers.append(p.id)
        state = initial_state("分析所选论文", context={"accessible_paper_ids": papers})
        state.update(await workflow.prepare_node(state))
        self.assertEqual(len(workflow._targets(state)), 5)
        self.assertTrue(any("超出部分" in g for g in state["gaps"]))

    async def test_revoked_selection_is_checked_even_when_read_cached(self):
        state = initial_state("分析", task_id="revoked", context=self.context)
        state.update(await workflow.prepare_node(state))
        state.update(await workflow.read_node(state))
        state["accessible_paper_ids"] = []
        result = await workflow.read_node(state)
        self.assertIn("范围", result["stop_reason"])

    async def test_revocation_discards_previously_collected_quotes(self):
        state = initial_state("分析", task_id="revoke-quotes", context=self.context)
        state.update(await workflow.prepare_node(state))
        state.update(await workflow.read_node(state))
        state.update(await workflow.quote_node(state))
        self.assertTrue(state["evidence_pool"])
        state["accessible_paper_ids"] = []
        revoked = workflow.check_access(state)
        self.assertEqual(revoked["evidence_pool"], {})
        self.assertEqual(revoked["workflow_rows"], [])
        self.assertNotIn(self.local.id, {p["id"] for p in revoked["papers"]})

    async def test_partial_source_failure_retains_usable_papers(self):
        self.search.return_value["source_errors"] = {"semantic_scholar": "429 限流"}
        result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.assertEqual(result["status"], "done")
        self.assertIn(public_paper(2).id, result["discovered_paper_ids"])
        self.assertIn("限流", result["stop_reason"])
        self.assertTrue(result["evidence"])

    async def test_new_query_reads_new_papers_without_rereading_old_ones(self):
        self.assessments = [{"coverage": "partial", "missing": ["不同方法"], "next_action": "search_more",
                             "new_query": "alternative method", "rationale": "缺少不同方法"}]
        self.search.side_effect = [
            {"papers": [public_paper(2)], "source_errors": {}},
            {"papers": [public_paper(2), public_paper(3)], "source_errors": {}}]
        with patch("app.agents.workflow._read", wraps=workflow._read) as read:
            result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.assertEqual(read.await_count, 2)
        self.assertEqual(set(result["reading_status"]), {public_paper(2).id, public_paper(3).id})
        self.assertEqual(self.search.await_args_list[1].args[0], "alternative method")

    async def test_repeated_expand_requests_stop_at_search_budget(self):
        self.assessments = [{"coverage": "partial", "missing": ["材料"], "next_action": "paginate", "rationale": "更多"}] * 4
        result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.search.await_count, 3)
        self.assertEqual(result["extra_search"], 2)

    async def test_missing_evidence_reads_more_existing_candidates_before_search(self):
        self.search.return_value["papers"] = [public_paper(n) for n in range(2, 6)]
        self.assessments = [{"coverage": "partial", "missing": ["其他方法的依据"], "next_action": "read_next", "rationale": "需要补读"}]
        result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.search.assert_awaited_once()
        self.assertEqual(len(result["reading_status"]), 4)
        self.assertEqual(result["deep_reads"], 4)

    async def test_sqlite_restart_resumes_after_read_without_duplicate_download(self):
        with patch.dict(os.environ, {"RESEARCH_CHECKPOINT": "sqlite"}):
            await agent.reset_graph()
            original = workflow._read
            async def pause_after_read(state, pid):
                result = await original(state, pid)
                agent.request_pause(state["task_id"])
                return result
            with patch("app.agents.workflow._read", new=AsyncMock(side_effect=pause_after_read)) as read:
                first = await self.run_task(task_id="persistent")
                self.assertEqual(first["status"], "waiting")
                self.assertEqual(first["interrupt"]["type"], "paused")
                await agent.reset_graph()
                result = await agent.resume_task("persistent", True)
                self.assertEqual(read.await_count, 1)
            self.assertEqual(result["status"], "done")
            self.assertTrue(result["evidence"])
            await agent.reset_graph()

    async def test_background_progress_is_visible_to_polling_clients(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = workflow._read
        async def delayed_read(state, pid):
            entered.set()
            await release.wait()
            return await original(state, pid)
        with patch("app.agents.workflow._read", new=delayed_read):
            task = await agent.start_background("分析这篇论文", mode="react", auto_approve=True,
                                                task_id="poll-progress", context=self.context)
            await asyncio.wait_for(entered.wait(), 5)
            live = agent.get_task(task["task_id"])
            self.assertEqual(live["status"], "running")
            self.assertEqual(live["workflow_stage"], "prepare")
            release.set()
            await asyncio.wait_for(agent._BACKGROUND[task["task_id"]], 10)
        self.assertEqual(agent.get_task(task["task_id"])["status"], "done")

    async def test_invalid_model_json_does_not_trigger_repeat_downloads(self):
        original = self.complete
        async def malformed(messages):
            if "只复制原文" in messages[0]["content"]:
                return "not valid JSON"
            return await original(messages)
        self.model.side_effect = malformed
        with patch("app.agents.workflow._read", wraps=workflow._read) as read:
            result = await self.run_task()
        self.assertEqual(read.await_count, 1)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["evidence"])

    async def test_no_candidates_returns_explicit_insufficiency(self):
        self.search.return_value = {"papers": [], "source_errors": {}}
        result = await self.run_task("搜索论文", context={"accessible_paper_ids": []})
        self.assertEqual(result["status"], "done")
        self.assertIn("不能确认", result["answer"])
        self.assertEqual(self.synthesis_count, 0)

    async def test_public_fulltext_failure_preserves_abstract_boundary(self):
        with patch("app.services.analysis.fulltext.prepare", new=AsyncMock(return_value=None)):
            result = await self.run_task("搜索论文并分析方法", context={"accessible_paper_ids": []})
        self.assertEqual(result["reading_status"][public_paper(2).id]["basis"], "abstract")
        self.assertTrue(any("不能确认正文实验细节" in gap for gap in result["gaps"]))

    async def test_fabricated_answer_citations_are_removed_after_bounded_review(self):
        original = self.complete
        async def fabricated(messages):
            if "你是论文研究助手" in messages[0]["content"]:
                return json.dumps({"answer": "编造结论。[E9999]"})
            return await original(messages)
        self.model.side_effect = fabricated
        result = await self.run_task()
        self.assertEqual(result["judge_round"], 2)
        self.assertNotIn("编造结论", result["answer"])
        self.assertNotIn("[E9999]", result["answer"])
        self.assertNotEqual(result["review"]["final_status"], "verified")

    async def test_contradictory_claims_cannot_loop_forever(self):
        original = self.complete
        async def contradict(messages):
            if "独立评审" in messages[0]["content"]:
                data = json.loads(messages[-1]["content"])
                return json.dumps({"reviews": [{"claim_id": c["claim_id"], "verdict": "contradiction", "action": "drop",
                                                "issue": "与原文矛盾", "quote": c["evidence"][0]["quote"][:30]}
                                               for c in data["claims"]]})
            return await original(messages)
        self.model.side_effect = contradict
        result = await self.run_task()
        self.assertEqual(result["judge_round"], 2)
        self.assertIn("已移除", result["answer"])
        self.assertEqual(self.synthesis_count, 2)

    async def test_exhausted_search_budget_never_calls_provider(self):
        state = initial_state("搜索论文", task_id="no-budget")
        state["max_api_calls"] = 0
        result = await workflow.search_node(state)
        self.search.assert_not_awaited()
        self.assertEqual(result["workflow_action"], "synthesize")

    async def _workspace_operation(self, approved):
        second, _ = library.save_paper(public_paper(2), public_paper(2).id)
        third, _ = library.save_paper(public_paper(3), public_paper(3).id)
        self.model.side_effect = [json.dumps({"tool_calls": [{"name": "add_papers_to_workspace",
            "args": {"paper_ids": [second.id, third.id]}}]}), json.dumps({"answer": "已记录实际操作结果。"})]
        paused = await agent.run("将论文加入工作区", mode="react", auto_approve=True,
                                 task_id="operation", context=self.context)
        self.assertEqual(paused["status"], "waiting")
        self.assertEqual(paused["interrupt"]["type"], "confirm_workspace")
        self.assertEqual(len(workspaces.list_papers("w")["papers"]), 1)
        result = await agent.resume_task("operation", {"approved_ids": approved})
        self.assertEqual(result["status"], "done")
        return result

    async def test_workspace_write_uses_only_confirmed_subset(self):
        await self._workspace_operation([public_paper(2).id, "not-requested"])
        members = {row["paper"]["id"] for row in workspaces.list_papers("w")["papers"]}
        self.assertEqual(members, {self.local.id, public_paper(2).id})

    async def test_workspace_rejection_writes_nothing(self):
        result = await self._workspace_operation([])
        self.assertEqual(len(workspaces.list_papers("w")["papers"]), 1)
        self.assertTrue(any(op["status"] == "denied" for op in result["operations"]))

    async def test_repeated_operation_resume_is_rejected_without_duplicate_write(self):
        await self._workspace_operation([public_paper(2).id])
        with self.assertRaises(agent.TaskConflict):
            await agent.resume_task("operation", {"approved_ids": [public_paper(2).id]})
        self.assertEqual(len(workspaces.list_papers("w")["papers"]), 2)

    async def test_research_api_and_event_replay_expose_new_stages(self):
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/research/tasks", json={"goal": "分析这篇论文", "mode": "react",
                "auto_approve": True, **self.context})
            self.assertEqual(response.status_code, 200)
            task = response.json()
            self.assertTrue(task["evidence"])
            stream = await client.get(f"/api/research/tasks/{task['task_id']}/events")
            self.assertIn('"step": "quotes"', stream.text)
            self.assertIn('[DONE]', stream.text)

    async def test_live_stream_emits_stage_updates_and_final_state(self):
        events = [event async for event in agent.stream_run("分析这篇论文", mode="react", auto_approve=True,
                                                           task_id="stream", context=self.context)]
        nodes = {event.get("node") for event in events if event.get("type") == "node"}
        self.assertTrue({"prepare_research", "read_paper", "extract_quotes", "synthesize_evidence"} <= nodes)
        self.assertTrue(any(event.get("status") == "done" for event in events))

    def test_evidence_ids_never_recycle_after_eviction(self):
        state = initial_state("q")
        for i in range(MAX_EVIDENCE_ITEMS + 4):
            state.update(add_evidence(state, [{"paper_id": "p", "quote": f"original passage {i}"}])[0])
        self.assertEqual(len(state["evidence_pool"]), MAX_EVIDENCE_ITEMS)
        self.assertIn(f"E{MAX_EVIDENCE_ITEMS + 4:04d}", state["evidence_pool"])


if __name__ == "__main__":
    unittest.main()
