"""Model interpretation is validated, never replaced by lexical topic/count rules."""
import json
import unittest
from unittest.mock import AsyncMock, patch
from app.infrastructure import llm
from app.tools.decompose_question import decompose_question
from finding_fixtures import PLAN, dumps

class DecomposeQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_interpretation_and_context_are_used(self):
        history=['用户：研究句子表示。助手：你希望多少篇？']
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(PLAN))) as complete:
            result=await decompose_question('先给我来三份合适的材料',history)
        self.assertEqual(result['target_count'],3)
        self.assertEqual(result['resolved_question'],PLAN['resolved_question'])
        self.assertEqual(result['queries'],PLAN['queries'])
        payload=json.loads(complete.call_args.args[0][1]['content'])
        self.assertEqual(payload['context'],history)
        self.assertEqual(payload['question'],'先给我来三份合适的材料')

    async def test_model_dates_and_time_preference_preserved(self):
        plan={**PLAN,'constraints':{'year_from':2023,'year_to':2025,'temporal':'recent','requirements':['医疗场景']}}
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(plan))):
            result=await decompose_question('这几年相关的就行')
        self.assertEqual(result['constraints'],plan['constraints'])

    async def test_missing_topic_is_a_model_clarification(self):
        plan={**PLAN,'queries':[],'core_topics':[],'needs_clarification':True,'clarification_question':'想找哪个研究主题？'}
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(plan))):
            result=await decompose_question('接着来')
        self.assertEqual(result['status'],'needs_clarification')
        self.assertEqual(result['queries'],[])

    async def test_invalid_plan_retries_then_fails_without_keyword_fallback(self):
        for response in ['not json',dumps({**PLAN,'queries':['x'*301]}),dumps({**PLAN,'constraints':{'year_from':2025,'year_to':2020}})]:
            with self.subTest(response=response),patch.object(llm,'complete',new=AsyncMock(return_value=response)) as complete:
                result=await decompose_question('找5篇RAG医疗应用论文')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['queries'],[])
            self.assertEqual(complete.await_count,2)

    async def test_provider_failure_is_explicit(self):
        with patch.object(llm,'complete',new=AsyncMock(side_effect=llm.AnalysisError('unavailable'))):
            result=await decompose_question('找论文')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['llm_calls'],1)
