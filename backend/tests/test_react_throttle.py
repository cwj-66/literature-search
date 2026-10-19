"""ReAct 历史节流：search_papers 工具结果超量时只把精简候选注入消息历史。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent, tooling


def entry(i):
    return {"id": f"arxiv:{i}", "title": f"Paper {i}", "abstract": f"abstract {i}", "year": 2023}


class CompactToolContentTests(unittest.TestCase):
    def test_search_results_above_head_are_truncated(self):
        content = json.dumps({"found": 15, "papers": [entry(i) for i in range(15)], "workspace_total": 15},
                             ensure_ascii=False)
        compacted = json.loads(tooling.compact_tool_content("search_papers", content))
        self.assertEqual(len(compacted["papers"]), tooling.SEARCH_RESULT_HEAD)
        self.assertEqual(compacted["truncated"], 5)
        self.assertEqual(compacted["papers"][0], {"id": "arxiv:0", "title": "Paper 0", "year": 2023})

    def test_search_results_within_head_strip_abstract_without_truncation(self):
        content = json.dumps({"found": 3, "papers": [entry(i) for i in range(3)]}, ensure_ascii=False)
        compacted = json.loads(tooling.compact_tool_content("search_papers", content))
        self.assertEqual(len(compacted["papers"]), 3)
        self.assertTrue(all("abstract" not in paper for paper in compacted["papers"]))
        self.assertNotIn("truncated", compacted)

    def test_already_compact_results_are_unchanged(self):
        content = json.dumps({"found": 1, "papers": [{"id": "p1", "title": "Paper", "year": 2023}]})
        self.assertEqual(tooling.compact_tool_content("search_papers", content), content)

    def test_other_tools_untouched(self):
        content = json.dumps({"found": 15, "papers": [entry(i) for i in range(15)]}, ensure_ascii=False)
        self.assertEqual(tooling.compact_tool_content("get_paper_details", content), content)


class ToolNodeThrottleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_tool_node_compacts_large_search_result_in_messages(self):
        state = {
            **agent.initial_state("goal"),
            "messages": [
                {"role": "assistant", "content": "{}", "tool_calls": [
                    {"id": "call_1", "name": "search_papers", "args": {"query": "q"}, "type": "tool_call"},
                ]},
            ],
        }
        found = {"papers": [entry(i) for i in range(15)], "source_errors": {}, "source_retry_after": {}}
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=found)):
            result = await agent.tool_node(state)
        payload = json.loads(result["messages"][0]["content"])
        self.assertEqual(len(payload["papers"]), tooling.SEARCH_RESULT_HEAD)
        self.assertEqual(payload["truncated"], 5)
        # 完整候选仍进入状态，不受影响。
        self.assertEqual(len(result["papers"]), 15)


if __name__ == "__main__":
    unittest.main()
