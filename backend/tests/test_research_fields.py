import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from app.infrastructure import llm
from app.infrastructure.db import connection
from app.infrastructure.user_scope import scope
from app.schemas import AnalysisFields, PaperOut, ResearchField
from app.services.analysis import analysis_store, fulltext
from app.services.papers import library, workspaces
from app.services.research_fields import store, evolution

def field(canonical='Retrieval-Augmented Generation'):
    return ResearchField(name='检索增强生成', canonical_name=canonical, aliases=['RAG'])

def papers():
    end=datetime.now(timezone.utc).year
    return [PaperOut(id=f'arxiv:2401.0000{i}', title=f'Public paper {i}', year=year,
        abstract=f'We propose a grounded retrieval method number {i}. Long context evaluation remains for future work.', sources=['arxiv'])
        for i,year in enumerate([end-6,end-3,end],1)]

def draft():
    pool=papers()
    evidence=lambda i:[{'paper_id':pool[i].id,'quote':pool[i].abstract.split('. ')[0]+'.'}]
    return {'summary':'Unsupported model memory must not become the overview.',
        'milestones':[{'paper_id':p.id,'kind':'foundation' if i==0 else 'breakthrough',
            'contribution':'提出有依据的检索方法。','significance':'模型归纳：样本中的方法演进。','evidence':evidence(i)} for i,p in enumerate(pool)],
        'hotspots':[{'title':'可验证的检索','description':'近期样本研究有依据的检索方法。','evidence':evidence(2)}],
        'directions':[{'title':'长上下文评估','description':'进一步探索长上下文条件下的方法。','uncertainty':'现有摘要尚未验证这一方向的实际收益。',
            'evidence':[{'paper_id':pool[2].id,'quote':'Long context evaluation remains for future work.'}]}], 'limitations':[]}

class FieldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'PAPER_DB_PATH':str(Path(self.tmp.name)/'papers.sqlite3'),'PAPER_USER_SESSIONS':'0'})
        env.start(); self.addCleanup(env.stop)
        self.paper,_=library.save_paper(papers()[0],papers()[0].id)
        for wid in ['one','two']:
            workspaces.register(wid,wid);workspaces.add(wid,self.paper.local_id)
    def assign(self):
        with connection() as db,db:store.save_assignments(db,self.paper.local_id,[field()])
        return store.workspace_fields('one')['fields'][0]['id']
    async def test_aliases_and_subfield_identity(self):
        self.assertEqual(store.register(field('RAG')),store.register(field()))
        self.assertEqual(store.register(field('检索增强生成')),store.register(field()))
        child=ResearchField(name='图检索增强生成',canonical_name='GraphRAG',parent='Retrieval-Augmented Generation')
        self.assertNotEqual(store.register(child),store.register(field()))
    async def test_coalescing_cross_workspace_reuse_and_failed_refresh(self):
        fid=self.assign()
        with ThreadPoolExecutor(max_workers=4) as pool:
            created=list(pool.map(lambda _:store.request_report(fid,'model'),range(6)))
        self.assertEqual(sum(created),1)
        job=store.claim_report();self.assertIsNone(store.claim_report())
        store.finish(job,{'summary':'Public API report','partial':False})
        self.assertEqual(store.workspace_fields('two')['fields'][0]['analysis']['report']['summary'],'Public API report')
        self.assertFalse(store.request_report(fid,'model'))
        store.request_report(fid,'model',True);store.finish(store.claim_report(),error='Failed refresh')
        self.assertEqual(store.report(fid)['report']['summary'],'Public API report')
    async def test_multi_label_assignments_and_workspace_removal(self):
        with connection() as db,db:store.save_assignments(db,self.paper.local_id,[field(),ResearchField(name='知识图谱',canonical_name='Knowledge Graphs')])
        self.assertEqual(len(store.workspace_fields('one')['fields']),2)
        workspaces.remove('one',[self.paper.local_id])
        self.assertEqual(store.workspace_fields('one')['fields'],[])
        self.assertEqual(len(store.workspace_fields('two')['fields']),2)
    async def test_previous_year_report_remains_visible_during_refresh(self):
        fid=self.assign()
        with patch.object(store,'cache_key',return_value='prior-year'):
            store.request_report(fid,'model');store.finish(store.claim_report(),{'summary':'Prior public report','partial':False})
        self.assertTrue(store.report(fid)['stale'])
        store.request_report(fid,'model')
        self.assertEqual(store.report(fid)['report']['summary'],'Prior public report')
        self.assertIsNotNone(store.report(fid)['computed_at'])
        store.finish(store.claim_report(),error='Temporary failure')
        self.assertEqual(store.report(fid)['report']['summary'],'Prior public report')
    async def test_user_assignments_and_reports_are_both_isolated(self):
        fid=self.assign();store.request_report(fid,'model');store.finish(store.claim_report(),{'summary':'Public only','partial':False})
        with scope('a'*32):
            workspaces.register('one','Different visitor')
            self.assertEqual(store.workspace_fields('one')['fields'],[])
            with self.assertRaises(ValueError):store.require_membership('one',fid)
            p,_=library.save_paper(papers()[1],papers()[1].id);workspaces.add('one',p.local_id)
            with connection() as db,db:store.save_assignments(db,p.local_id,[field()])
            self.assertIsNone(store.workspace_fields('one')['fields'][0]['analysis']['report'])
    async def test_api_authorization_and_no_inline_model(self):
        from app.main import app
        fid=self.assign();workspaces.register('empty','empty')
        with patch.object(llm,'complete',new=AsyncMock(side_effect=AssertionError('No inline model'))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post(f'/api/workspaces/one/fields/{fid}/analyze',json={})
                self.assertEqual(response.status_code,200);self.assertEqual(response.json()['status'],'queued')
                self.assertEqual((await client.get(f'/api/workspaces/empty/fields/{fid}/report')).status_code,404)
                self.assertEqual((await client.get('/api/workspaces/missing/fields')).status_code,404)
    async def test_successful_paper_analysis_saves_fields_in_same_transaction(self):
        task=analysis_store.create_task(self.paper.local_id,{self.paper.id},self.paper.local_id,False,'model','version').task_row
        analysis_store.transition(task['task_id'],'pending','preparing');analysis_store.transition(task['task_id'],'preparing','analyzing')
        result=AnalysisFields(research_fields=[field()],research_problem='Problem',main_content='Content',technical_method='Method',innovations=['Contribution'],experimental_results='Unknown',limitations='Unknown',keywords=['RAG'],analysis_basis='abstract')
        prepared=SimpleNamespace(degradation_reason=None,source_kind='abstract',source_url=None,evidence={})
        self.assertTrue(analysis_store.save_success(task['task_id'],self.paper.local_id,result,prepared,'model','version'))
        self.assertEqual(store.workspace_fields('one')['fields'][0]['name'],'检索增强生成')
    async def test_cancel_and_stale_lease_recovery(self):
        fid=self.assign();store.request_report(fid,'model');store.finish(store.claim_report(),cancelled=True)
        self.assertEqual(store.report(fid)['status'],'queued');store.claim_report()
        with store.global_db() as db,db:db.execute('UPDATE field_reports SET started_at=0')
        self.assertIsNotNone(store.claim_report())

    async def test_adding_reused_analysis_backfills_fields_once_without_retrying_failures(self):
        task=analysis_store.create_task(self.paper.local_id,{self.paper.id},self.paper.local_id,False,'model','version').task_row
        analysis_store.transition(task['task_id'],'pending','preparing');analysis_store.transition(task['task_id'],'preparing','analyzing')
        result=AnalysisFields(research_fields=[],research_problem='Problem',main_content='Content',technical_method='Method',innovations=['Contribution'],experimental_results='Unknown',limitations='Unknown',keywords=['retrieval'],analysis_basis='abstract')
        prepared=SimpleNamespace(degradation_reason=None,source_kind='abstract',source_url=None,evidence={})
        analysis_store.save_success(task['task_id'],self.paper.local_id,result,prepared,'model','version')
        with connection() as db,db:db.execute('DELETE FROM field_identifications')
        workspaces.add('one',self.paper.local_id)
        self.assertEqual(store.workspace_fields('one')['identifying_count'],1)
        self.assertEqual(store.queue_legacy('one',retry_failed=False),0)
        with connection() as db,db:db.execute("UPDATE field_identifications SET status='failed'")
        workspaces.add('one',self.paper.local_id)
        self.assertEqual(store.workspace_fields('one')['failed_count'],1)
        self.assertEqual(store.queue_legacy('one'),1)
        with connection() as db,db:store.save_assignments(db,self.paper.local_id,[])
        workspaces.add('one',self.paper.local_id)
        self.assertEqual(store.workspace_fields('one')['legacy_count'],0)

    async def test_existing_labels_are_not_counted_or_queued_as_unidentified(self):
        task=analysis_store.create_task(self.paper.local_id,{self.paper.id},self.paper.local_id,False,'model','version').task_row
        analysis_store.transition(task['task_id'],'pending','preparing');analysis_store.transition(task['task_id'],'preparing','analyzing')
        result=AnalysisFields(research_fields=[field()],research_problem='Problem',main_content='Content',technical_method='Method',innovations=['Contribution'],experimental_results='Unknown',limitations='Unknown',keywords=['RAG'],analysis_basis='abstract')
        prepared=SimpleNamespace(degradation_reason=None,source_kind='abstract',source_url=None,evidence={})
        self.assertTrue(analysis_store.save_success(task['task_id'],self.paper.local_id,result,prepared,'model','version'))
        # Older demo data has valid assignments without a corresponding job marker.
        with connection() as db,db:db.execute('DELETE FROM field_identifications WHERE local_id=?',(self.paper.local_id,))
        state=store.workspace_fields('one')
        self.assertEqual(len(state['fields']),1)
        self.assertEqual(state['unidentified_count'],0)
        self.assertEqual(state['legacy_count'],0)
        self.assertEqual(store.queue_legacy('one'),0)
    async def test_grounding_rejects_invented_quotes_and_old_hotspots(self):
        raw=draft();raw['milestones'][0]['evidence'][0]['quote']='This quote never occurred in the source.'
        raw['hotspots'][0]['evidence']=raw['milestones'][1]['evidence']
        rows=evolution.ground(evolution.Draft.model_validate(raw),papers(),datetime.now(timezone.utc).year)
        self.assertEqual(len([r for r in rows if r['section']=='milestones']),2)
        self.assertFalse(any(r['section']=='hotspots' for r in rows))
    async def test_two_round_api_search_and_semantic_review(self):
        responses=[{'queries':[]},{'queries':[{'query':'Seminal exact title','purpose':'breakthrough'}]},draft(),{'accepted':[0,1,2,3,4]}]
        with patch.object(evolution.search,'search_papers',new=AsyncMock(return_value={'papers':papers(),'source_errors':{}})) as searcher,patch.object(llm,'complete',new=AsyncMock(side_effect=[json.dumps(r) for r in responses])):
            result=await evolution.discover({'id':'field','name':'检索增强生成','canonical_name':'Retrieval-Augmented Generation'})
        self.assertEqual([m['year'] for m in result['milestones']],sorted(p.year for p in papers()))
        self.assertNotIn('Unsupported',result['summary'])
        self.assertEqual(searcher.call_args_list[0].args[3],'date_asc')
        self.assertEqual(searcher.call_args_list[1].args[3],'date')
        self.assertEqual(searcher.call_args_list[2].args[0],'Seminal exact title')
    async def test_all_sources_fail_without_fabricated_report(self):
        with patch.object(evolution.search,'search_papers',new=AsyncMock(side_effect=TimeoutError)),patch.object(llm,'complete',new=AsyncMock(return_value='{"queries":[]}')) as model:
            with self.assertRaises(llm.AnalysisError):await evolution.discover({'id':'field','name':'Test','canonical_name':'Test field'})
        self.assertEqual(model.await_count,1)
    async def test_targeted_foundation_survives_temporal_sample_budget(self):
        seed=papers()[0].model_copy(update={'id':'arxiv:seminal','year':2020})
        early=[seed.model_copy(update={'id':f'old:{i}','year':1990+i}) for i in range(12)]
        recent=[seed.model_copy(update={'id':f'new:{i}','year':datetime.now(timezone.utc).year}) for i in range(12)]
        output=draft();output['milestones']=[{**output['milestones'][0],'paper_id':seed.id,
            'evidence':[{'paper_id':seed.id,'quote':seed.abstract.split('. ')[0]+'.'}]}]
        output['hotspots']=[];output['directions']=[]
        replies=[{'queries':[{'query':'Exact seminal paper title','purpose':'foundation'}]}, {'queries':[]},output,{'accepted':[0]}]
        batches=[{'papers':early},{'papers':recent},{'papers':[seed]}]
        with patch.object(evolution.search,'search_papers',new=AsyncMock(side_effect=batches)) as searcher,patch.object(llm,'complete',new=AsyncMock(side_effect=[json.dumps(r) for r in replies])):
            report=await evolution.discover({'id':'field','name':'领域','canonical_name':'Some field'})
        self.assertEqual(searcher.call_args_list[2].args[3],'relevance')
        self.assertEqual(report['milestones'][0]['paper_id'],seed.id)
        self.assertLessEqual(report['coverage']['analyzed_count'],24)
    async def test_deduplication_by_doi(self):
        a=papers()[0];a.doi='10.1234/example';b=a.model_copy(update={'id':'doi:10.1234/example'})
        pool=[];evolution.add_candidates(pool,[a,b]);self.assertEqual(len(pool),1)
    async def test_every_structured_step_requests_json_explicitly(self):
        with patch.object(llm,'complete',new=AsyncMock(return_value='{"queries":[]}')) as model:
            await evolution.structured(evolution.prompts.GAPS,{'papers':[]},evolution.Plan)
        self.assertIn('json',model.call_args.args[0][0]['content'].lower())
    async def test_inline_extraction_uses_existing_model_calls(self):
        quote='We introduce retrieval augmented generation for grounded answers.'
        evidence=[{'paragraph_id':'P0001','quote':quote}]
        output={'claims':[{'kind':'main_content','text':'提出检索增强生成方法。','evidence':evidence},
            {'kind':'research_field','text':'检索增强生成','research_field':field().model_dump(),'evidence':evidence}]}
        prepared=fulltext.PreparedText(quote,'abstract','abstract',None,None,{'sections':[]})
        with patch.object(llm,'complete',new=AsyncMock(side_effect=[json.dumps(output),json.dumps({'reviews':[{'index':0,'supported':True},{'index':1,'supported':True}]})])) as model:
            result=await llm.analyze(papers()[0],prepared)
        self.assertEqual(len(result.research_fields),1);self.assertEqual(model.await_count,2)
