import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import llm


class ModelResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_model_is_sent_to_provider(self):
        with patch.dict(os.environ, {"LLM_MODEL": "qwen3.7-flash-2026-07-15"}):
            _, payload = await self.request({"choices": [{"message": {"content": "{}"}}]})
        self.assertEqual(payload["model"], "qwen3.7-flash-2026-07-15")

    def test_model_switch_does_not_reuse_previous_model_cache(self):
        from app.agents.store import cache_key
        with patch.dict(os.environ, {"LLM_MODEL": "previous-model"}):
            old = cache_key("router", {"goal": "same question"})
        with patch.dict(os.environ, {"LLM_MODEL": "qwen3.7-flash-2026-07-15"}):
            new = cache_key("router", {"goal": "same question"})
        self.assertNotEqual(old, new)

    async def request(self, data, base="https://dashscope.aliyuncs.com/compatible-mode/v1"):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, json=data)
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "synthetic-key", "DASHSCOPE_BASE_URL": base}), \
             patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client):
            answer = await llm.complete([{"role": "user", "content": "Synthetic test"}])
        return answer, client.post.call_args.kwargs["json"]

    async def test_dashscope_uses_answer_budget_instead_of_hidden_reasoning(self):
        answer, payload = await self.request({"choices": [{"finish_reason": "stop", "message": {"content": '{"claims":[]}'}}]})
        self.assertEqual(answer, '{"claims":[]}')
        self.assertIs(payload["enable_thinking"], False)

    async def test_other_providers_do_not_receive_dashscope_option(self):
        _, payload = await self.request({"choices": [{"message": {"content": "{}"}}]}, "https://example.test/v1")
        self.assertNotIn("enable_thinking", payload)

    async def test_reasoning_only_truncation_has_specific_error(self):
        with self.assertRaisesRegex(llm.AnalysisError, "长度上限"):
            await self.request({"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "synthetic reasoning"}}], "usage": {"completion_tokens": 8192}})

    async def test_valid_looking_json_with_truncation_is_not_accepted(self):
        with self.assertRaisesRegex(llm.AnalysisError, "长度上限"):
            await self.request({"choices": [{"finish_reason": "length", "message": {"content": '{"claims":[]}'}}]})

    async def test_empty_answer_is_never_replaced_with_reasoning(self):
        with self.assertRaisesRegex(llm.AnalysisError, "未返回有效分析内容"):
            await self.request({"choices": [{"finish_reason": "stop", "message": {"content": None, "reasoning_content": "not an answer"}}]})
