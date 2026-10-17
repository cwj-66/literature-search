import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import llm
from app.schemas import SourceParagraph
from app.services.analysis import analysis, checkpoints, evidence_analysis as ea


SUCCESS = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, responses):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = responses
        sleeper = AsyncMock()
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "synthetic"}), \
             patch("app.infrastructure.llm.httpx.AsyncClient", return_value=client), \
             patch("app.infrastructure.llm.asyncio.sleep", sleeper):
            try:
                answer = await llm.complete([])
            except llm.AnalysisError as error:
                answer = error
        return answer, client.post.await_count, sleeper

    async def test_429_respects_retry_after(self):
        result, count, sleeper = await self.call([httpx.Response(429, json={}, headers={"Retry-After": "3"}), httpx.Response(200, json=SUCCESS)])
        self.assertEqual(result, "{}")
        self.assertEqual(count, 2)
        sleeper.assert_awaited_once_with(3.0)

    async def test_rate_quota_is_temporary_not_a_balance_error(self):
        result, count, _ = await self.call([httpx.Response(429, json={"error": {"code": "Throttling.RateQuota"}}), httpx.Response(200, json=SUCCESS)])
        self.assertEqual(result, "{}")
        self.assertEqual(count, 2)

    async def test_503_then_connection_error_recovers_with_bounded_attempts(self):
        result, count, sleeper = await self.call([httpx.Response(503, json={}), httpx.ConnectError("synthetic"), httpx.Response(200, json=SUCCESS)])
        self.assertEqual(result, "{}")
        self.assertEqual(count, 3)
        self.assertEqual(sleeper.await_count, 2)

    async def test_transport_failure_stops_after_three_attempts(self):
        result, count, _ = await self.call([httpx.ReadTimeout("synthetic")] * 3)
        self.assertIsInstance(result, llm.AnalysisError)
        self.assertEqual(count, 3)

    async def test_auth_quota_and_large_cooldown_do_not_retry(self):
        for response in [httpx.Response(401, json={}), httpx.Response(429, json={"error": {"code": "insufficient_quota"}}),
                         httpx.Response(429, json={}, headers={"Retry-After": "300"}),
                         httpx.Response(503, json={}, headers={"x-should-retry": "false"})]:
            result, count, sleeper = await self.call([response])
            self.assertIsInstance(result, llm.AnalysisError)
            self.assertEqual(count, 1)
            sleeper.assert_not_awaited()

    async def test_context_limit_is_typed_and_not_retried_unchanged(self):
        result, count, _ = await self.call([httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})])
        self.assertIsInstance(result, llm.ContextLimitError)
        self.assertEqual(count, 1)

    def test_retry_header_variants_and_invalid_values(self):
        self.assertEqual(llm.retry_delay({"retry-after-ms": "1500"}, 0), 1.5)
        with patch("app.infrastructure.llm.time.time", return_value=0):
            self.assertEqual(llm.retry_delay({"retry-after": "Thu, 01 Jan 1970 00:00:05 GMT"}, 0), 5)
        self.assertIsNone(llm.retry_delay({"retry-after": "1e1000"}, 0))
        self.assertIsNone(llm.retry_delay({"retry-after": "nan"}, 0))
        self.assertLessEqual(llm.retry_delay({"retry-after": "invalid"}, 2), 4)

    async def test_process_concurrency_is_capped(self):
        gate = llm._model_gate()
        peak = active = 0
        async def task():
            nonlocal peak, active
            async with gate:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
        await asyncio.gather(*(task() for _ in range(6)))
        self.assertEqual(peak, 2)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_limit_splits_and_preserves_both_decisions(self):
        claims = [ea.AnalysisClaim(kind="main_content", text="测试结论", evidence=[]) for _ in range(2)]
        responses = [llm.OutputLimitError("synthetic limit"), '{"reviews":[{"index":0,"supported":true}]}', '{"reviews":[{"index":0,"supported":false}]}']
        with patch.object(llm, "complete", AsyncMock(side_effect=responses)) as model:
            result = await ea.review(claims)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], claims[0])
        self.assertEqual(model.await_count, 3)

    async def test_output_limit_splits_without_losing_source_ids(self):
        source = [SourceParagraph(id="P0001", section="Method", text="The first original source paragraph."),
                  SourceParagraph(id="P0002", section="Method", text="The second original source paragraph.")]
        async def extract(messages):
            items = json.loads(messages[-1]["content"].split("<paper_text>\n")[1].split("\n</paper_text>")[0])
            if len(items) > 1:
                raise llm.OutputLimitError("synthetic limit")
            p = items[0]
            return ea.ExtractionOutput.model_validate({"claims": [{"kind": "main_content", "text": "测试结论", "evidence": [{"paragraph_id": p["id"], "quote": p["text"]}]}]})
        with patch.object(ea, "extract", AsyncMock(side_effect=extract)) as model:
            result = await ea.extract_chunk(source, "", False)
        self.assertEqual(model.await_count, 3)
        self.assertEqual([c.evidence[0].paragraph_id for c in result], ["P0001", "P0002"])

    async def test_unsplittable_chunk_fails_without_looping(self):
        with patch.object(ea, "extract", AsyncMock(side_effect=llm.OutputLimitError("limit"))) as model:
            with self.assertRaises(llm.OutputLimitError):
                await ea.extract_chunk([SourceParagraph(id="P0001", section="Method", text="Original source material.")], "", False)
        self.assertEqual(model.await_count, 1)

    async def test_failed_run_reuses_checkpoint_success_then_clears_it(self):
        messages = [{"role": "user", "content": "Synthetic test material"}]
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(temp) / "check.sqlite3")}):
            with patch.object(llm, "complete", AsyncMock(return_value='{"claims":[]}')) as model:
                with self.assertRaises(llm.AnalysisError):
                    with checkpoints.enabled():
                        await ea.extract(messages)
                        raise llm.AnalysisError("Later step failed")
                self.assertEqual(model.await_count, 1)
            with patch.object(llm, "complete", AsyncMock(side_effect=AssertionError("Should reuse"))):
                with checkpoints.enabled():
                    await ea.extract(messages)
            with patch.object(llm, "complete", AsyncMock(return_value='{"claims":[]}')) as model:
                with checkpoints.enabled():
                    await ea.extract(messages)
                self.assertEqual(model.await_count, 1)

    async def test_task_deadline_has_actionable_failure(self):
        async def timeout(coro, timeout):
            coro.close()
            raise TimeoutError
        with patch.object(analysis.asyncio, "wait_for", side_effect=timeout):
            with self.assertRaisesRegex(llm.AnalysisError, "15分钟"):
                await analysis.analyze_prepared(None, None)
