"""Provider-native tool protocol stays separate from JSON-only analysis calls."""
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import llm
from app.tools.finding_tools import FUNCTION_TOOLS


class NativeToolProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, data):
        response = httpx.Response(200, json=data)
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY":"synthetic-server-key", "DASHSCOPE_BASE_URL":"https://dashscope.aliyuncs.com/compatible-mode/v1"}), \
             patch.object(llm.httpx, "AsyncClient", return_value=client):
            result = await llm.complete_with_tools([{"role":"user", "content":"find papers"}], FUNCTION_TOOLS)
        return result, client.post.call_args.kwargs

    async def test_native_calls_sent_without_json_response_format(self):
        message = {"content":None, "tool_calls":[{"id":"call-1", "type":"function", "function":{"name":"search_papers", "arguments":'{"query":"image generation"}'}}]}
        result, request = await self.request({"choices":[{"finish_reason":"tool_calls", "message":message}]})
        self.assertEqual(result["tool_calls"][0]["id"], "call-1")
        self.assertEqual(request["json"]["tools"], FUNCTION_TOOLS)
        self.assertNotIn("response_format", request["json"])
        self.assertFalse(request["json"]["enable_thinking"])
        self.assertFalse(request["json"]["parallel_tool_calls"])
        self.assertEqual(request["headers"]["Authorization"], "Bearer synthetic-server-key")

    async def test_text_answer_ends_without_fake_tool_calls(self):
        result, _ = await self.request({"choices":[{"message":{"content":"无法继续搜索，来源暂不可用。"}}]})
        self.assertEqual(result["role"], "assistant")
        self.assertNotIn("tool_calls", result)

    async def test_bad_argument_string_is_preserved_for_runner_correction(self):
        message = {"tool_calls":[{"id":"call-1", "function":{"name":"search_papers", "arguments":"not json"}}]}
        result, _ = await self.request({"choices":[{"message":message}]})
        self.assertEqual(result["tool_calls"][0]["function"]["arguments"], "not json")

    async def test_object_arguments_are_normalized_for_native_history(self):
        message = {"tool_calls":[{"id":"call-1", "function":{"name":"search_papers", "arguments":{"query":"image generation"}}}]}
        result, _ = await self.request({"choices":[{"message":message}]})
        self.assertEqual(json.loads(result["tool_calls"][0]["function"]["arguments"]), {"query":"image generation"})

    async def test_duplicate_call_ids_rejected_before_execution(self):
        calls = [{"id":"same", "function":{"name":"search_papers", "arguments":"{}"}}]*2
        with self.assertRaises(llm.AnalysisError):
            await self.request({"choices":[{"message":{"tool_calls":calls}}]})

    async def test_truncated_tool_arguments_are_not_executed(self):
        with self.assertRaises(llm.OutputLimitError):
            await self.request({"choices":[{"finish_reason":"length", "message":{"content":"partial", "tool_calls":[]}}]})

    async def test_empty_answer_does_not_use_hidden_reasoning(self):
        with self.assertRaises(llm.AnalysisError):
            await self.request({"choices":[{"message":{"content":None, "reasoning_content":"not an answer"}}]})
