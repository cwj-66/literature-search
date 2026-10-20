"""Native researcher acceptance: exercise the public graph with controlled tools."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent, discovery, finding
from app.infrastructure import llm
from app.main import app
from app.tools import finding_tools


def call(name, args, ident="call-1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def paper(ident="arxiv:2401.00001", year=2024, abstract="Retrieves image patches during autoregressive image generation."):
    return {"id": ident, "title": "Synthetic autoregressive image generation", "authors": ["Synthetic Author"],
            "year": year, "abstract": abstract, "url": "https://example.test/paper", "sources": ["arxiv"]}


def report(papers=None, failure=False):
    failures = [{"source": "semantic_scholar", "status": "rate_limited", "http_status": 429, "retryable": False,
                 "message": "Semantic Scholar 限流", "retry_after_seconds": 120, "paper_count": 0}] if failure else []
    return {"status": "partial" if failure else "completed", "papers": [paper()] if papers is None else papers,
            "sources": [{"source": "arxiv", "status": "completed", "paper_count": 1}, *failures],
            "notice": [], "source_errors": {"semantic_scholar": "限流"} if failure else {},
            "source_retry_after": {"semantic_scholar": 120} if failure else {}, "omitted_unknown_year": 0}


class NativeFindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name)/"papers.sqlite3"), "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def state(self):
        return finding._start(agent.initial_state("找2023年以来自回归patch检索增强图像生成论文，排除视觉问答", year_from=2023))

    async def invoke(self, state, name, args, ident="tool-1"):
        state["finding_pending"] = call(name, args, ident)["tool_calls"]
        updated = await finding.tools_node(state)
        return updated, json.loads(updated["finding_messages"][-1]["content"])

    async def run_graph(self, outputs, result=None):
        with patch.object(llm, "complete", new=AsyncMock(return_value=json.dumps({"mode":"react", "research_intent":"discover"}))), \
             patch.object(llm, "complete_with_tools", new=AsyncMock(side_effect=outputs)) as model, \
             patch.object(finding_tools, "search_candidates", new=AsyncMock(return_value=result or report())) as search, \
             patch.object(discovery, "select_bibliography", side_effect=AssertionError("no lexical selection")), \
             patch.object(discovery, "protect_query", side_effect=AssertionError("no lexical topic rewriting")), \
             patch("app.tools.decompose_question.decompose_question", new=AsyncMock(side_effect=AssertionError("no plan gate"))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/research/tasks", json={"goal":"找2023年以来patch级别自回归检索增强图像生成论文，排除VQA", "year_from":2023})
            self.assertEqual(response.status_code, 200, response.text)
        return response.json(), model, search

    async def test_public_graph_search_then_submit_with_native_history(self):
        result, model, search = await self.run_graph([
            call("search_papers", {"query":"autoregressive image generation retrieved patches", "year_from":2023}),
            call("submit_papers", {"items":[{"paper_id":paper()["id"], "reason":"摘要说明自回归图像生成时检索patch，符合需求"}], "note":"找到一篇匹配论文，不用无关条目凑数。"}, "call-2")])
        self.assertEqual(result["paper_ids"], [paper()["id"]])
        self.assertEqual(result["recommended"][0]["url"], paper()["url"])
        self.assertEqual(result["search_count"], 1)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(search.await_count, 1)
        self.assertEqual(result["reading_status"], {})
        self.assertIsNone(result["interrupt"])
        history = model.await_args_list[1].args[0]
        self.assertEqual(history[-1]["role"], "tool")
        self.assertEqual(history[-1]["tool_call_id"], "call-1")
        self.assertIn("abstract", json.loads(history[-1]["content"])["papers"][0])
        self.assertEqual(sum(e["step"] == "search" for e in result["events"]), 1)

    async def test_public_graph_preserves_partial_source_failure(self):
        result, _, _ = await self.run_graph([
            call("search_papers", {"query":"retrieved image patches"}),
            call("submit_papers", {"items":[{"paper_id":paper()["id"], "reason":"符合图像生成条件"}]}, "call-2")], report(failure=True))
        self.assertEqual(len(result["recommended"]), 1)
        self.assertIn("429", result["answer"])
        self.assertIn("Semantic Scholar", result["answer"])
        self.assertEqual(result["source_status"][0]["status"], "rate_limited")

    async def test_native_year_error_corrected_without_plan_gate_or_network(self):
        state = self.state()
        with patch.object(finding_tools, "search_candidates", new=AsyncMock(return_value=report())) as fetch:
            state, failed = await self.invoke(state, "search_papers", {"query":"image generation", "year_from":"since 2023"})
            self.assertTrue(failed["error"]["retryable"])
            self.assertEqual(failed["error"]["fields"][0]["field"], "year_from")
            self.assertEqual(fetch.await_count, 0)
            state, good = await self.invoke(state, "search_papers", {"query":"image generation", "year_from":2023})
            self.assertEqual(good["status"], "completed")
            self.assertEqual(fetch.await_count, 1)
        self.assertNotIn("temporal", finding_tools.SearchArgs.model_fields)

    async def test_unknown_id_and_blank_reason_cannot_submit(self):
        state = self.state()
        state, result = await self.invoke(state, "submit_papers", {"items":[{"paper_id":"invented-id", "reason":"fabricated"}]})
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertEqual(state["recommended_ids"], [])
        state, result = await self.invoke(state, "submit_papers", {"items":[{"paper_id":"invented-id", "reason":" "}]})
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertFalse(state["finding_done"])

    async def test_duplicate_query_reuses_result_third_is_refused(self):
        state = self.state()
        with patch.object(finding_tools, "search_candidates", new=AsyncMock(return_value=report())) as fetch:
            state, _ = await self.invoke(state, "search_papers", {"query":"image generation"})
            state, cached = await self.invoke(state, "search_papers", {"query":"IMAGE   generation"})
            state, refused = await self.invoke(state, "search_papers", {"query":"image generation"})
        self.assertTrue(cached["reused"])
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(refused["error"]["code"], "repeated_query")
        self.assertEqual(state["search_count"], 1)

    async def test_unknown_year_and_old_papers_are_excluded(self):
        state = self.state()
        rows = [paper("arxiv:2201.00001", 2022), paper("arxiv:2401.00003", None), paper()]
        with patch.object(finding_tools, "search_candidates", new=AsyncMock(return_value=report(rows))):
            state, output = await self.invoke(state, "search_papers", {"query":"image generation"})
        self.assertEqual([p["id"] for p in state["papers"]], [paper()["id"]])
        self.assertEqual(output["omitted_unknown_year"], 1)

    async def test_details_of_unseen_paper_do_not_hit_provider(self):
        with patch.object(finding_tools, "paper_detail", new=AsyncMock()) as detail:
            _, output = await self.invoke(self.state(), "get_paper_detail", {"paper_id":"invented"})
        self.assertEqual(detail.await_count, 0)
        self.assertEqual(output["error"]["code"], "invalid_arguments")

    async def test_missing_abstract_details_are_fetched_once(self):
        state = self.state()
        state["papers"] = [paper(abstract=None)]
        with patch.object(finding_tools, "paper_detail", new=AsyncMock(return_value={"status":"completed", "paper":paper()})) as detail:
            state, first = await self.invoke(state, "get_paper_detail", {"paper_id":paper()["id"]})
            state, second = await self.invoke(state, "get_paper_detail", {"paper_id":paper()["id"]})
        self.assertEqual(detail.await_count, 1)
        self.assertTrue(second["reused"])
        self.assertEqual(first["paper"]["abstract"], paper()["abstract"])

    async def test_final_model_failure_does_not_promote_unjudged_candidates(self):
        result, _, _ = await self.run_graph([call("search_papers", {"query":"image generation"}), llm.AnalysisError("模型暂不可用")])
        self.assertEqual(result["recommended"], [])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("模型暂不可用", result["answer"])

    async def test_no_tools_ends_without_parsing_prose_into_papers(self):
        result, _, search = await self.run_graph([{"role":"assistant", "content":"当前信息不足，请补充你关注的方法。"}])
        self.assertEqual(result["recommended"], [])
        self.assertEqual(search.await_count, 0)

    async def test_round_cap_keeps_candidates_but_no_unsubmitted_recommendations(self):
        state = self.state()
        state.update(finding_turns=8, papers=[paper()])
        with patch.object(llm, "complete_with_tools", new=AsyncMock()) as model:
            result = await finding.agent_node(state)
        self.assertEqual(model.await_count, 0)
        self.assertEqual(result["recommended_ids"], [])
        self.assertIn("轮次上限", result["stop_reason"])

    async def test_empty_submission_allowed_after_all_sources_fail(self):
        state = self.state()
        failed = report([], True)
        failed["sources"][0].update(status="timeout", message="arXiv请求超时")
        failed["status"] = "failed"
        with patch.object(finding_tools, "search_candidates", new=AsyncMock(return_value=failed)):
            state, _ = await self.invoke(state, "search_papers", {"query":"image generation"})
        state, _ = await self.invoke(state, "submit_papers", {"items":[]})
        self.assertTrue(state["finding_done"])
        self.assertIn("不能据此判断", state["answer"])
        self.assertIn("429", state["answer"])

    async def test_submit_stops_later_calls_in_same_batch(self):
        state = self.state()
        state["finding_pending"] = [*call("submit_papers", {"items":[]})["tool_calls"],
                                    *call("search_papers", {"query":"image generation"}, "call-2")["tool_calls"]]
        with patch.object(finding_tools, "search_candidates", new=AsyncMock()) as fetch:
            result = await finding.tools_node(state)
        self.assertEqual(fetch.await_count, 0)
        self.assertEqual(len(result["finding_messages"]), 2)
        self.assertEqual(json.loads(result["finding_messages"][-1]["content"])["error"]["code"], "already_submitted")

    async def test_parameter_corrections_and_search_budget_are_bounded(self):
        state = self.state()
        for _ in range(4):
            state, output = await self.invoke(state, "search_papers", {"query":"image generation", "year_from":"since 2023"})
        self.assertEqual(output["error"]["code"], "argument_retries_exhausted")
        state = self.state()
        state["search_count"] = finding._search_limit(state)
        with patch.object(finding_tools, "search_candidates", new=AsyncMock()) as fetch:
            state, output = await self.invoke(state, "search_papers", {"query":"new expression"})
        self.assertEqual(output["error"]["code"], "search_budget")
        self.assertEqual(fetch.await_count, 0)

    async def test_timeout_does_not_execute_pending_search(self):
        state = self.state()
        state["finding_started_at"] = time.time() - 181
        with patch.object(finding_tools, "search_candidates", new=AsyncMock()) as fetch:
            result, output = await self.invoke(state, "search_papers", {"query":"image generation"})
        self.assertEqual(fetch.await_count, 0)
        self.assertTrue(result["finding_done"])
        self.assertEqual(output["error"]["code"], "stopped")

    def test_cross_source_duplicates_keep_exposed_id_and_merge_abstract(self):
        a = {**paper(), "arxiv_id":"2401.00001", "doi":"10.1/synthetic", "abstract":None}
        b = {**paper("s2:synthetic"), "doi":"10.1/synthetic", "sources":["semantic_scholar"]}
        pool, fresh, updated, aliases, duplicates = finding_tools.merge_candidates([a], [b], {})
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["id"], a["id"])
        self.assertEqual(pool[0]["abstract"], b["abstract"])
        self.assertEqual(fresh, [])
        self.assertEqual(duplicates, 1)
        self.assertEqual(aliases[b["id"]], a["id"])

    async def test_discussion_does_not_enter_finding_loop(self):
        with patch.object(llm, "complete", new=AsyncMock(side_effect=[json.dumps({"mode":"direct", "interaction":"discuss"}), json.dumps({"answer":"上一轮只找到两篇，原因是摘要未满足排除条件。"})])), \
             patch.object(llm, "complete_with_tools", new=AsyncMock(side_effect=AssertionError("discussion must not search"))):
            result = await agent.run("为什么只有两篇", context={"conversation_history":"上轮推荐两篇，理由是自回归patch生成，Semantic Scholar429。"})
        self.assertEqual(result["search_count"], 0)
        self.assertEqual(result["mode"], "direct")

    async def test_stateless_submission_cannot_bypass_seen_ids(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/research/tools/submit_papers", json={"arguments":{"items":[]}})
        self.assertEqual(result.status_code, 409)
