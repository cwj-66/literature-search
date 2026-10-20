"""Model judgments, source identity, and failures govern recommendation membership."""
import json
import unittest
from unittest.mock import AsyncMock,patch
from app.infrastructure import llm
from app.tools.rerank_papers import rerank_papers
from finding_fixtures import CANDIDATES,PLAN,selection,dumps

class RerankPapersTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_issue_year_is_not_rewritten_or_used_as_online_publication_proof(self):
        from datetime import datetime, timedelta, timezone
        today = datetime.now(timezone(timedelta(hours=8))).date()
        candidate = {**CANDIDATES[0], "year": today.year + 1}
        with patch.object(llm, 'complete', new=AsyncMock(return_value=dumps(selection(1, (0,))))) as complete:
            result = await rerank_papers('找近期论文', [candidate], 1)
        messages = complete.call_args.args[0]
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['today'], today.isoformat())
        self.assertEqual(payload['candidates'][0]['year'], today.year + 1)
        self.assertIn('预排刊期', messages[0]['content'])
        self.assertEqual(result['recommended'][0]['year'], today.year + 1)

    async def test_semantically_related_title_without_literal_terms_is_accepted(self):
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(selection()))) as complete:
            result=await rerank_papers(PLAN['resolved_question'],CANDIDATES,3,groups=[['text embedding']])
        self.assertEqual(result['recommended_ids'],['arxiv:1','arxiv:2','arxiv:3'])
        self.assertEqual(result['remaining_candidates'],['arxiv:4'])
        self.assertEqual(result['recommended'][0]['title'],CANDIDATES[0]['title'])
        payload=json.loads(complete.call_args.args[0][1]['content'])
        self.assertEqual(payload['research_goal'],PLAN['resolved_question'])
        self.assertEqual(len(payload['candidates']),4)

    async def test_single_candidate_is_scored_and_not_automatically_recommended(self):
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(selection(1,())))) as complete:
            result=await rerank_papers('找向量模型论文',[CANDIDATES[3]],5)
        complete.assert_awaited_once()
        self.assertEqual(result['recommended'],[])
        self.assertFalse(result['enough'])

    async def test_failed_or_incomplete_judgments_never_fall_back_to_pool_head(self):
        for response in ['invalid',dumps(selection(1)),dumps({'judgments':[{'index':900,'score':10,'relevant':True,'reason':'injected'}],'summary':'wrong'})]:
            with self.subTest(response=response),patch.object(llm,'complete',new=AsyncMock(return_value=response)):
                result=await rerank_papers('找向量模型论文',CANDIDATES)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['recommended'],[])
            self.assertEqual(len(result['remaining_candidates']),4)

    async def test_provider_failure_preserves_unverified_candidates(self):
        with patch.object(llm,'complete',new=AsyncMock(side_effect=llm.AnalysisError('unavailable'))):
            result=await rerank_papers('找论文',CANDIDATES)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['llm_calls'],1)
        self.assertEqual(result['recommended'],[])

    async def test_years_filter_before_model_and_early_order_before_limit(self):
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(selection(3,(0,1,2))))) as complete:
            result=await rerank_papers('找早期向量模型',[CANDIDATES[2],CANDIDATES[1],CANDIDATES[0],CANDIDATES[3]],1,
                                       constraints={'year_from':2020,'temporal':'early'})
        self.assertEqual(result['recommended_ids'],['arxiv:1'])
        self.assertEqual(len(json.loads(complete.call_args.args[0][1]['content'])['candidates']),3)

    async def test_missing_abstract_is_explicit_and_score_must_be_relevant(self):
        rows=[{**CANDIDATES[0],'abstract':None},CANDIDATES[1]]
        judged=selection(2,(0,1)); judged['judgments'][1]['score']=3
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(judged))):
            result=await rerank_papers('找论文',rows,5)
        self.assertEqual(result['recommended_ids'],['arxiv:1'])
        self.assertIn('无摘要',result['reasons']['arxiv:1'])
