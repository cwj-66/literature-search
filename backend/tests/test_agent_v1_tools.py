"""Registered tools share the same model-backed implementation as the finding graph."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from app.agents import tooling
from app.infrastructure import llm
from app.tools import research
from finding_fixtures import PLAN,CANDIDATES,selection,dumps

class AgentV1ToolHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_decompose_handler_writes_model_plan_without_rewriting(self):
        state={'goal':'找论文','llm_calls':0,'queries':['stale query'],'messages':[]}
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(PLAN))):
            content,updates=await tooling._tool_decompose_question(state,{'question':state['goal']})
        self.assertEqual(updates['queries'],PLAN['queries'])
        self.assertEqual(updates['result_limit'],PLAN['target_count'])
        self.assertEqual(updates['llm_calls'],1)

    async def test_failed_decomposition_does_not_install_a_fake_plan(self):
        state={'goal':'找论文','llm_calls':0,'messages':[]}
        with patch.object(llm,'complete',new=AsyncMock(side_effect=llm.AnalysisError('failed'))):
            content,updates=await tooling._tool_decompose_question(state,{'question':state['goal']})
        self.assertEqual(json.loads(content)['status'],'failed')
        self.assertNotIn('queries',updates)
        self.assertEqual(updates['llm_calls'],1)

    async def test_rerank_handler_uses_model_selection_and_provenance(self):
        state={'goal':'找向量模型论文','papers':CANDIDATES,'search_plan':PLAN,'llm_calls':0,'source_errors':{}}
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(selection()))):
            content,updates=await tooling._tool_rerank_papers(state,{'resolved_question':PLAN['resolved_question']})
        self.assertEqual(updates['recommended_ids'],['arxiv:1','arxiv:2','arxiv:3'])
        self.assertEqual(json.loads(content)['recommended_ids'],updates['recommended_ids'])
        self.assertEqual(updates['papers'][0]['rank_score'],9)

    async def test_source_failures_remain_visible_in_tool_report(self):
        state={'goal':'找论文','papers':CANDIDATES,'llm_calls':0,'source_errors':{'semantic_scholar':'timeout'}}
        with patch.object(llm,'complete',new=AsyncMock(return_value=dumps(selection()))):
            content,_=await tooling._tool_rerank_papers(state,{'resolved_question':PLAN['resolved_question'],'target_count':5})
        self.assertIn('来源',json.loads(content)['shortfall_reason'])

    async def test_empty_pool_reports_failure(self):
        content,updates=await tooling._tool_rerank_papers({'papers':[]},{'resolved_question':'找论文'})
        self.assertEqual(json.loads(content)['status'],'failed')
        self.assertNotIn('recommended_ids',updates)

    def test_tools_registered_and_budgeted(self):
        for name in ('decompose_question','rerank_papers'):
            self.assertIn(name,research.CATALOG)
            self.assertIn(name,tooling.TOOL_REGISTRY)
            self.assertIn(name,tooling.LLM_TOOLS)
