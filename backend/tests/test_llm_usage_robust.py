"""LLM 响应健壮性：畸形 usage 字段不得使 complete 抛出原生异常。"""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.infrastructure import llm


def response_with_usage(usage):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": '{"a":1}'}, "finish_reason": "stop"}],
        "usage": usage,
    })


class MalformedUsageTests(unittest.IsolatedAsyncioTestCase):
    async def _complete(self, usage, meter=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response_with_usage(usage)))
        token = llm.attach_usage_meter(meter) if meter is not None else None
        try:
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
                 patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client):
                return await llm.complete([{"role": "user", "content": "hi"}])
        finally:
            if token is not None:
                llm.detach_usage_meter(token)

    async def test_usage_as_list_is_ignored(self):
        content = await self._complete([1, 2, 3], meter=llm.UsageMeter())
        self.assertEqual(json.loads(content), {"a": 1})

    async def test_usage_non_numeric_tokens_are_ignored(self):
        content = await self._complete({"prompt_tokens": "abc", "completion_tokens": None}, meter=llm.UsageMeter())
        self.assertEqual(json.loads(content), {"a": 1})

    async def test_meter_records_with_missing_usage(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})))
        meter = llm.UsageMeter()
        token = llm.attach_usage_meter(meter)
        try:
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
                 patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client):
                await llm.complete([{"role": "user", "content": "hi"}])
        finally:
            llm.detach_usage_meter(token)
        self.assertEqual(meter.total, 0)


if __name__ == "__main__":
    unittest.main()
