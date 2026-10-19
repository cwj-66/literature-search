"""Token usage metering for LLM calls and task-level reporting."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent
from app.infrastructure import llm


def chat_response(content: str, prompt_tokens: int, completion_tokens: int) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    })


class UsageMeterUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_records_usage_into_active_meter(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: chat_response('{"a":1}', 11, 7)))
        meter = llm.UsageMeter()
        token = llm.attach_usage_meter(meter)
        try:
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
                 patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client):
                await llm.complete([{"role": "user", "content": "hi"}])
        finally:
            llm.detach_usage_meter(token)
        self.assertEqual(meter.prompt_tokens, 11)
        self.assertEqual(meter.completion_tokens, 7)
        self.assertEqual(meter.total, 18)

    async def test_complete_without_meter_keeps_working(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: chat_response('{"a":1}', 3, 2)))
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
             patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client):
            content = await llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(json.loads(content), {"a": 1})
        self.assertIsNone(llm.usage_meter())


class TaskTokenReportingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_run_reports_tokens_used_from_meter(self):
        calls = []

        async def fake_complete(messages):
            calls.append(1)
            meter = llm.usage_meter()
            if meter is not None:
                meter.add(5, 3)
            if "任务路由员" in str(messages[0].get("content", "")):
                return json.dumps({"mode": "direct", "rationale": "按用户请求分类"})
            return json.dumps({"answer": "你好"})

        with patch.object(llm, "complete", new=AsyncMock(side_effect=fake_complete)):
            result = await agent.run("你好", mode="direct")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["tokens_used"], 8 * len(calls))
