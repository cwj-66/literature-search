"""The actual public API, rather than manual node invocation, uses the three tools."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import httpx
from app.agents import agent,discovery,finding
from app.main import app
from app.infrastructure import llm
from finding_fixtures import PLAN,CANDIDATES,selection,search_result,dumps

class AgentV1FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PAPER_DB_PATH':str(Path(self.temp.name)/'papers.sqlite3'),'RESEARCH_CHECKPOINT':'memory'})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph(); self.env.stop(); self.temp.cleanup()

    async def run_flow(self,plan=None,judgments=None,result=None):
        outputs=[dumps({'mode':'react','interaction':'act','research_intent':'discover','domains':['discovery']}),
                 dumps(plan or PLAN),dumps(judgments or selection())]
        with patch.object(llm,'complete',new=AsyncMock(side_effect=outputs)) as model, \
             patch('app.services.retrieval.search.search_papers',new=AsyncMock(return_value=result or search_result())) as search, \
             patch.object(discovery,'select_bibliography',side_effect=AssertionError('no lexical selection')), \
             patch.object(discovery,'protect_query',side_effect=AssertionError('no lexical rewriting')), \
             patch.object(agent.workflow,'_read',new=AsyncMock(side_effect=AssertionError('no PDF reading'))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post('/api/research/tasks',json={'goal':'先给我来三份向量模型相关的材料','mode':'auto','auto_approve':False})
            self.assertEqual(response.status_code,200,response.text)
        return response.json(),model,search

    async def test_public_request_uses_model_plan_search_and_model_selection(self):
        result,model,search=await self.run_flow()
        self.assertEqual(result['recommended_ids'] if 'recommended_ids' in result else result['paper_ids'],['arxiv:1','arxiv:2','arxiv:3'])
        self.assertEqual(result['result_limit'],3)
        self.assertEqual(result['candidate_count'],4)
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual([e['step'] for e in result['events']],['route','prepare','search','select','stop'])
        self.assertEqual(model.await_count,3)
        self.assertEqual(search.await_count,1)
        self.assertEqual(result['reading_status'],{})
        self.assertIsNone(result['interrupt'])

    async def test_source_failure_is_not_hidden_by_successful_recommendations(self):
        result,_,_=await self.run_flow(result=search_result(errors={'semantic_scholar':'timeout'}))
        self.assertEqual(len(result['recommended']),3)
        self.assertIn('semantic_scholar',result['source_errors'])
        self.assertIn('部分数据源',result['answer'])

    async def test_failed_selection_does_not_promote_candidates_at_finish(self):
        with patch.object(llm,'complete',new=AsyncMock(side_effect=[
            dumps({'mode':'react','research_intent':'discover'}),dumps(PLAN),llm.AnalysisError('failed')])), \
             patch('app.services.retrieval.search.search_papers',new=AsyncMock(return_value=search_result())):
            result=await agent.run('找三篇向量模型论文',auto_approve=True)
        self.assertEqual(result['recommended'],[])
        self.assertEqual(len(result['candidates']),4)
        self.assertIn('筛选失败',result['stop_reason'])

    async def test_supplemental_search_preserves_goal_and_merges_duplicate_ids(self):
        outputs=[dumps({'mode':'react','research_intent':'discover'}),dumps(PLAN),
                 dumps(selection(2,(0,))),dumps(selection())]
        with patch.object(llm,'complete',new=AsyncMock(side_effect=outputs)) as model, \
             patch('app.services.retrieval.search.search_papers',new=AsyncMock(side_effect=[search_result(CANDIDATES[:2]),search_result()])) as search:
            result=await agent.run('找三篇向量模型论文',auto_approve=True)
        self.assertEqual(search.await_count,2)
        self.assertEqual(result['candidate_count'],4)
        self.assertEqual(len(result['recommended']),3)
        for call in (model.call_args_list[2],model.call_args_list[3]):
            self.assertEqual(json.loads(call.args[0][1]['content'])['research_goal'],PLAN['resolved_question'])

    async def test_discussion_does_not_search(self):
        with patch.object(llm,'complete',new=AsyncMock(side_effect=[dumps({'mode':'direct','interaction':'discuss'}),dumps({'answer':'这次没找够，不代表没有论文。'})])), \
             patch('app.services.retrieval.search.search_papers',new=AsyncMock()) as search:
            result=await agent.run('是找不到吗',context={'conversation_history':'找5篇向量模型论文，找到2篇'})
        search.assert_not_awaited()
        self.assertEqual(result['search_count'],0)

    async def test_cancel_before_plan_never_calls_tools(self):
        with patch.object(finding,'decompose_question',new=AsyncMock()) as tool:
            result=await finding.plan_node({**agent.initial_state('找论文'),'cancelled':True})
        tool.assert_not_awaited()
        self.assertEqual(result['recommended_ids'],[])
