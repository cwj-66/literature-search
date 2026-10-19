"""End-to-end agent v1 flow: one sentence → decompose → search → rerank → answer."""

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent
from app.infrastructure import llm

GOAL = "帮我找三个有关 RAG 应用的论文"

PLAN = {
    "queries": ['("retrieval-augmented generation") AND ("application")'],
    "exclusions": [],
    "search_plan": {"core_concepts": ["retrieval-augmented generation"],
                    "must_have_facets": [["application"]], "likely_synonyms": []},
    "_llm_used": 1,
}

CANDIDATES = [
    {"id": "arxiv:rag1", "title": "Retrieval-Augmented Generation for Customer Support",
     "abstract": "We deploy retrieval-augmented generation in a real-world customer support application.",
     "year": 2024, "url": "https://arxiv.org/abs/rag1"},
    {"id": "arxiv:rag2", "title": "RAG for Medical Applications",
     "abstract": "retrieval-augmented generation applied to clinical application scenarios.",
     "year": 2023, "url": "https://arxiv.org/abs/rag2"},
    {"id": "arxiv:rag3", "title": "Applying RAG to Legal Document Review",
     "abstract": "we apply retrieval augmented generation to legal application workflows.",
     "year": 2025, "url": "https://arxiv.org/abs/rag3"},
    {"id": "pubmed:imm1", "title": "RAG gene deficiency in mice",
     "abstract": "RAG recombination defects impair V(D)J recombination in lymphocytes.",
     "year": 2021, "url": "https://pubmed/imm1"},
]


def score_all(papers, query, *, min_candidates=20):
    return [{**paper, "rank_score": 9} for paper in papers], 1


def merge(state: dict, updates: dict) -> dict:
    merged = dict(state)
    for key, value in updates.items():
        if key == "messages":
            merged["messages"] = list(state.get("messages") or []) + list(value)
        else:
            merged[key] = value
    return merged


class AgentV1FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_papers_three_step_flow(self):
        state = agent.initial_state(GOAL, context={"accessible_paper_ids": []})
        state.update({"mode": "react", "task_kind": "research", "domains": ["*"]})
        react_outputs = [
            json.dumps({"thought": "拆解需求", "tool_calls": [{"name": "decompose_question", "args": {"question": GOAL}}]}),
            json.dumps({"thought": "检索候选", "tool_calls": [{"name": "search_papers", "args": {
                "query": '("retrieval-augmented generation") AND ("application")', "page_size": 10}}]}),
            json.dumps({"thought": "筛选推荐", "tool_calls": [{"name": "rerank_papers", "args": {
                "resolved_question": GOAL, "target_count": 3}}]}),
            json.dumps({"thought": "完成", "answer": "推荐：arxiv:rag1、arxiv:rag2、arxiv:rag3。"}),
        ]
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value={
                 "papers": CANDIDATES, "source_errors": {}, "source_retry_after": {}})), \
             patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)), \
             patch.object(llm, "complete", new=AsyncMock(side_effect=react_outputs)) as complete:
            for _ in range(6):
                out = await agent.react_agent(state)
                state = merge(state, out)
                last = (state.get("messages") or [])[-1]
                tool_calls = last.get("tool_calls") if isinstance(last, dict) else None
                if not tool_calls:
                    break
                state = merge(state, await agent.tool_node(state))
        self.assertEqual(state["recommended_ids"], ["arxiv:rag1", "arxiv:rag2", "arxiv:rag3"])
        self.assertNotIn("pubmed:imm1", state["recommended_ids"])
        self.assertEqual(len(state.get("recommendation_reasons") or {}), 3)
        self.assertTrue(state.get("queries"))
        answer = state.get("answer") or ""
        mentioned = {pid for pid in state["recommended_ids"] if pid in answer}
        self.assertEqual(mentioned, set(state["recommended_ids"]))
        self.assertEqual(complete.await_count, 4)

    async def test_discuss_followup_does_not_research(self):
        history = "用户：帮我找5篇有关向量大模型的论文\n助手：本次推荐2篇"
        stack = ExitStack()
        for node in ("plan_node", "react_agent", "tool_node", "claims_node", "judge_node"):
            stack.enter_context(patch.object(agent, node, new=AsyncMock(side_effect=AssertionError(f"must skip {node}"))))
        with stack, patch.object(llm, "complete", new=AsyncMock(side_effect=[
                json.dumps({"mode": "direct", "interaction": "discuss", "rationale": "解释上轮结果"}),
                json.dumps({"answer": "这次只筛选出两篇，不代表领域只有两篇。"})])):
            result = await agent.run("是找不到吗", context={"conversation_history": history,
                                                          "accessible_paper_ids": []})
        self.assertEqual(result["mode"], "direct")
        self.assertEqual(result["search_count"], 0)
        self.assertEqual(result["recommended"], [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()
