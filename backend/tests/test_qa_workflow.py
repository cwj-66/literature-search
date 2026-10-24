import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.retrieval import qa_workflow as qa
from app.services.analysis import attribution as att


def hit(pid, cid=None, text=None, basis="full_text"):
    return {"paper_id":pid,"chunk_id":cid or pid+"-1","text":text or f"The method in {pid} uses retrieval and verification to reduce errors.",
            "basis":basis,"section":"Methods","paragraph_ids":["P1"],"rerank_score":.9}


def planned(ids, **updates):
    return qa.Plan(resolved_question="比较所选论文的方法",queries=["method"],paper_ids=ids,
                   question_type="comparison",strategy="per_paper",dimensions=["核心方法"],**updates)


class QAWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"PAPER_DB_PATH":str(Path(self.temp.name)/"papers.sqlite3")})
        self.env.start()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    async def test_simple_fact_avoids_planner(self):
        retrieve=AsyncMock(return_value=[hit("p1")])
        with patch.object(qa.llm,"complete",new=AsyncMock()) as model:
            hits,meta=await qa.retrieve_evidence("研究对象是什么",["p1"],[],retrieve)
        model.assert_not_awaited()
        self.assertEqual(meta["rounds"],1)
        self.assertEqual(len(hits),1)

    async def test_single_complex_problem_can_decompose(self):
        payload={"question_type":"deep","strategy":"decompose","resolved_question":"如何实现检索方法", "queries":["retrieval procedure","verification mechanism"],"paper_ids":["p1"],"dimensions":["实现机制"]}
        with patch.object(qa.llm,"complete",new=AsyncMock(return_value=json.dumps(payload))) as model:
            plan=await qa.plan_question("具体如何实现",["p1"],[],qa.Budget())
        self.assertEqual(plan.strategy,"decompose");self.assertEqual(len(plan.queries),2)
        self.assertEqual(model.await_count,1)

    async def test_paper_ids_are_sql_scope_not_cross_paper_search_terms(self):
        ids=['arxiv:2401.12345','arxiv:2402.12345']
        plan=planned(ids).model_copy(update={'queries':[pid+' research goal core method' for pid in ids]})
        with patch.object(qa.llm,'complete',new=AsyncMock(return_value=plan.model_dump_json())):
            result=await qa.plan_question('比较研究目标和方法',ids,[],qa.Budget())
        self.assertEqual(result.queries,['research goal core method'])
        self.assertEqual(result.paper_ids,ids)

    async def test_bad_plans_safely_fall_back(self):
        base=planned(["p1"]).model_dump()
        cases=["not JSON",json.dumps({**base,"paper_ids":["private"]}),
               json.dumps({**base,"queries":["a","b","c","d"]}),
               json.dumps({**base,"queries":[""]}),json.dumps({**base,"queries":["x"*501]})]
        for raw in cases:
            with self.subTest(raw=raw[:50]),patch.object(qa.llm,"complete",new=AsyncMock(return_value=raw)):
                plan=await qa.plan_question("具体如何实现",["p1"],[],qa.Budget())
            self.assertEqual(plan.queries,["具体如何实现"]);self.assertEqual(plan.paper_ids,["p1"])

    async def test_configured_query_budget_and_timeout_fall_back(self):
        output=planned(['p1']).model_copy(update={'queries':['a','b']}).model_dump()
        with patch.object(qa.llm,'complete',new=AsyncMock(return_value=json.dumps(output))):
            result=await qa.plan_question('比较方法',['p1'],[],qa.Budget(queries=1))
        self.assertEqual(result.queries,['比较方法'])
        with patch.object(qa.llm,'complete',new=AsyncMock(side_effect=TimeoutError())):
            result=await qa.plan_question('如何实现',['p1'],[],qa.Budget())
        self.assertEqual(result.queries,['如何实现'])

    async def test_reference_is_resolved_from_provided_history_only(self):
        output=qa.Plan(resolved_question="查询改写方法需要什么数据",queries=["query rewriting training data"],paper_ids=["p1"]).model_dump()
        with patch.object(qa.llm,"complete",new=AsyncMock(return_value=json.dumps(output))) as model:
            plan=await qa.plan_question("它需要什么数据",["p1"],["查询改写使用什么模型"],qa.Budget())
        self.assertIn("查询改写",plan.resolved_question)
        payload=json.loads(model.call_args.args[0][1]["content"])
        self.assertEqual(payload["selected_paper_ids"],["p1"])
        self.assertEqual(payload["previous_questions"],["查询改写使用什么模型"])

    async def test_comparison_and_common_search_each_paper(self):
        for kind in ("comparison","common"):
            plan=planned(["p1","p2"]);plan.question_type=kind
            async def search(q,ids,**kw):
                return [hit(ids[0],ids[0]+str(i)) for i in range(4)]+[hit("unselected")]
            retrieve=AsyncMock(side_effect=search)
            with patch.object(qa,"plan_question",new=AsyncMock(return_value=plan)),patch.object(qa,"assess",new=AsyncMock(return_value=qa.Coverage(status="sufficient"))):
                hits,meta=await qa.retrieve_evidence("比较这些论文",["p1","p2"],[],retrieve)
            self.assertEqual({h["paper_id"] for h in hits},{"p1","p2"})
            self.assertEqual([h["paper_id"] for h in hits[:2]],["p1","p2"])
            self.assertEqual({tuple(c.args[1]) for c in retrieve.call_args_list},{("p1",),("p2",)})
            self.assertEqual(meta["rounds"],1)

    async def test_decomposed_queries_all_contribute_before_one_query_fills_context(self):
        plan=qa.Plan(resolved_question='方法与实验',queries=['method','experiment','limitations'],paper_ids=['p1'],question_type='deep',strategy='decompose')
        async def search(q,ids,**kw):return [hit('p1',q+str(i)) for i in range(4)]
        with patch.object(qa,'plan_question',new=AsyncMock(return_value=plan)),patch.object(qa,'assess',new=AsyncMock(return_value=qa.Coverage(status='sufficient'))):
            hits,_=await qa.retrieve_evidence('解释方法与实验',['p1'],[],search)
        self.assertEqual({h['retrieval_query'] for h in hits},set(plan.queries))

    async def test_correction_happens_at_most_once_and_stays_scoped(self):
        calls=[]
        async def search(q,ids,**kw):
            calls.append((q,ids));return [hit(ids[0])]
        partial=qa.Coverage(status="partial",missing_paper_ids=["p2"],correction_queries=["missing method"])
        with patch.object(qa,"plan_question",new=AsyncMock(return_value=planned(["p1","p2"]))),patch.object(qa,"assess",new=AsyncMock(return_value=partial)) as assess,patch.object(qa.rerank,"rerank",new=AsyncMock(side_effect=qa.rerank.RerankError("offline"))):
            hits,meta=await qa.retrieve_evidence("比较方法",["p1","p2"],[],search)
        self.assertEqual(meta["rounds"],2);self.assertEqual(len(calls),3)
        self.assertEqual(calls[-1][1],["p2"]);self.assertEqual(assess.await_count,2)
        self.assertTrue(meta["coverage_gap"])
        self.assertEqual(len({h["chunk_id"] for h in hits}),len(hits))

    async def test_corrective_subquestion_survives_global_rerank(self):
        async def search(q,ids,**kw):
            return [hit('p1',q+str(i)) for i in range(4)]
        partial=qa.Coverage(status='partial',correction_queries=['method steps','module input'])
        async def reversed_rank(q,docs,top_n):return [(i,.9) for i in reversed(range(len(docs)))]
        with patch.object(qa,'plan_question',new=AsyncMock(return_value=planned(['p1']))), \
             patch.object(qa,'assess',new=AsyncMock(return_value=partial)), \
             patch.object(qa.rerank,'rerank',new=reversed_rank):
            hits,_=await qa.retrieve_evidence('解释具体机制',['p1'],[],search)
        self.assertTrue({'method steps','module input'}<={h['retrieval_query'] for h in hits})

    def test_method_steps_after_900_chars_reach_generation_evidence(self):
        text='Background. '*90+'Critical second-stage mechanism after the former truncation boundary.'
        selected=qa.select_context([hit('p1',text=text)],['p1'],qa.Budget())
        evidence=att.evidence_from_chunks('p1',selected)
        self.assertIn('Critical second-stage mechanism',evidence[0].quote)

    async def test_many_selected_papers_are_not_lost_to_global_top_k(self):
        ids=[f"p{i}" for i in range(40)]
        async def search(q,pids,**kw):return [hit(pids[0],pids[0]+str(i),"evidence "*300) for i in range(4)]
        with patch.object(qa,"plan_question",new=AsyncMock(return_value=planned(ids))),patch.object(qa,"assess",new=AsyncMock(return_value=qa.Coverage(status="sufficient"))):
            hits,meta=await qa.retrieve_evidence("共同点",ids,[],search)
        self.assertEqual({h["paper_id"] for h in hits},set(ids))
        self.assertEqual(meta["retrieval_calls"],40)
        self.assertLessEqual(len(hits),qa.Budget().evidence_chunks)
        self.assertLess(sum(len(h["text"]) for h in hits),qa.Budget().context_chars)

    async def test_empty_scope_does_not_retrieve(self):
        retrieve=AsyncMock()
        hits,meta=await qa.retrieve_evidence("问题",[],[],retrieve)
        self.assertFalse(hits);retrieve.assert_not_awaited();self.assertTrue(meta["coverage_gap"])

    async def test_abstract_cannot_certify_method_details(self):
        with patch.object(qa,"complete_json",new=AsyncMock(return_value={"status":"sufficient"})):
            coverage=await qa.assess(planned(["p1"]),"具体如何实现",["p1"],[hit("p1",basis="abstract")],qa.Budget())
        self.assertEqual(coverage.status,"partial");self.assertEqual(coverage.missing_paper_ids,["p1"])

    async def test_coverage_invalid_scope_cannot_trigger_external_search(self):
        with patch.object(qa,"complete_json",new=AsyncMock(return_value={"status":"partial","missing_paper_ids":["private"]})):
            coverage=await qa.assess(planned(["p1"]),"比较方法",["p1"],[hit("p1")],qa.Budget())
        self.assertEqual(coverage.status,"partial");self.assertNotIn("private",coverage.missing_paper_ids)

    async def test_missing_paper_is_explicit_and_not_filled_with_other_papers(self):
        async def search(q,ids,**kw):return [hit("p1")] if ids==["p1"] else []
        with patch.object(qa,"plan_question",new=AsyncMock(return_value=planned(["p1","p2"]))),patch.object(qa,"complete_json",new=AsyncMock(side_effect=ValueError("bad"))),patch.object(qa.rerank,"rerank",new=AsyncMock(return_value=[(0,.9)])):
            hits,meta=await qa.retrieve_evidence("共同点",["p1","p2"],[],search)
        self.assertEqual(meta["missing_paper_ids"],["p2"])
        self.assertTrue(any("不能纳入共同结论" in s for s in meta["limitations"]))
        self.assertEqual({h["paper_id"] for h in hits},{"p1"})


class JudgeAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_quote_error_is_repaired_then_judged_not_shown_as_code(self):
        invalid='{"answer":"The method uses "verification" [E0001]","claim_texts":[]}'
        async def judge(q,claims,evidence):
            self.assertEqual(claims[0]['claim'].text,'The method uses "verification" [E0001]')
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported',evidence_ids=['E0001']) for c in claims]
        with patch.object(att.llm,'complete',new=AsyncMock(return_value=invalid)) as model, \
             patch.object(qa,'retrieve_evidence',new=AsyncMock(return_value=([hit('p1')],{}))), \
             patch.object(att,'judge_claims',new=judge):
            result=await att.answer_question('method',['p1'])
        self.assertEqual(model.await_count,1)
        self.assertEqual(result['answer'],'The method uses "verification" [E0001]')

    async def test_failed_json_repair_withholds_raw_json(self):
        with patch.object(att.llm,'complete',new=AsyncMock(return_value='{broken JSON')) as model, \
             patch.object(qa,'retrieve_evidence',new=AsyncMock(return_value=([hit('p1')],{}))), \
             patch.object(att,'judge_claims',new=AsyncMock()) as judge:
            result=await att.answer_question('method',['p1'])
        self.assertEqual(model.await_count,2);judge.assert_not_awaited()
        self.assertNotIn('{broken',result['answer']);self.assertTrue(result['coverage_gap'])

    async def test_valid_but_unapproved_citation_is_replaced_by_judge_evidence(self):
        async def judge(q,claims,evidence):return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported',evidence_ids=['E0002']) for c in claims]
        with patch.object(att.llm,'complete',new=AsyncMock(return_value='{"answer":"Supported result [E0001]"}')),patch.object(qa,'retrieve_evidence',new=AsyncMock(return_value=([hit('p1'),hit('p2')],{}))),patch.object(att,'judge_claims',new=AsyncMock(side_effect=judge)):
            result=await att.answer_question('fact',['p1','p2'])
        self.assertNotIn('E0001',result['answer']);self.assertIn('E0002',result['answer'])
    async def test_long_answer_is_judged_in_full_not_truncated_to_extraction_claim_limit(self):
        answer='Supported method detail. '*150+' [E0001]'
        async def judge(q,claims,evidence):
            self.assertEqual(claims[-1]['claim'].text,answer)
            return [att.JudgeItem(claim_id=claims[-1]['claim_id'],verdict='supported',evidence_ids=['E0001'])]
        with patch.object(att.llm,'complete',new=AsyncMock(return_value=json.dumps({'answer':answer}))),patch.object(qa,'retrieve_evidence',new=AsyncMock(return_value=([hit('p1')],{}))),patch.object(att,'judge_claims',new=AsyncMock(side_effect=judge)):
            result=await att.answer_question('mechanism',['p1'])
        self.assertEqual(result['answer'],answer)
    async def test_rejected_claim_is_removed_from_actual_answer(self):
        result={"answer":"Supported result [E0001]. Invented 99% [E0001].","claim_texts":["Supported result","Invented 99%"]}
        async def judge(q,claims,evidence):
            return [att.JudgeItem(claim_id=c["claim_id"],verdict="supported" if c["claim"].text=="Supported result" else "insufficient",evidence_ids=["E0001"]) for c in claims]
        with patch.object(att.llm,"complete",new=AsyncMock(return_value=json.dumps(result))),patch.object(qa,"retrieve_evidence",new=AsyncMock(return_value=([hit("p1")],{}))),patch.object(att,"judge_claims",new=AsyncMock(side_effect=judge)) as checked:
            answer=await att.answer_question("result",["p1"])
        self.assertIn("Supported result",answer["answer"]);self.assertNotIn("99%",answer["answer"])
        self.assertLessEqual(checked.await_count,2)

    async def test_unreviewed_or_judge_failure_cannot_release_claims(self):
        for output in ([],RuntimeError("offline")):
            mock=AsyncMock(side_effect=output) if isinstance(output,Exception) else AsyncMock(return_value=output)
            with patch.object(att.llm,"complete",new=AsyncMock(return_value='{"answer":"Invented answer [E0001]"}')),patch.object(qa,"retrieve_evidence",new=AsyncMock(return_value=([hit("p1")],{}))),patch.object(att,"judge_claims",new=mock):
                answer=await att.answer_question("fact",["p1"])
            self.assertNotIn("Invented answer",answer["answer"]);self.assertTrue(answer["coverage_gap"])

    async def test_wrong_cross_paper_attribution_is_not_released(self):
        async def judge(q,claims,evidence):
            return [att.JudgeItem(claim_id=c["claim_id"],verdict="supported",evidence_ids=["E0001"]) for c in claims]
        meta={"plan":{"question_type":"common"}}
        with patch.object(att.llm,"complete",new=AsyncMock(return_value='{"answer":"两篇论文有共同机制 [E0001]"}')),patch.object(qa,"retrieve_evidence",new=AsyncMock(return_value=([hit("p1"),hit("p2")],meta))),patch.object(att,"judge_claims",new=AsyncMock(side_effect=judge)):
            result=await att.answer_question("共同机制",["p1","p2"])
        self.assertNotIn("两篇论文有共同机制",result["answer"])

    async def test_all_evidence_sent_to_judge_and_generation_and_invalid_ids_removed(self):
        hits=[hit(f"p{i}") for i in range(15)]
        async def complete(messages):
            text=messages[0]["content"]
            if "独立评审" in text:
                body=json.loads(messages[1]["content"])
                return json.dumps({"reviews":[{"claim_id":c["claim_id"],"verdict":"supported","evidence_ids":["E0015"]} for c in body["claims"]]})
            body=json.loads(messages[1]["content"])
            self.assertIn("E0015",body["evidence"])
            return '{"answer":"Final supported finding [E0015] 无效E99999"}'
        with patch.object(att.llm,"complete",new=AsyncMock(side_effect=complete)),patch.object(qa,"retrieve_evidence",new=AsyncMock(return_value=(hits,{}))):
            result=await att.answer_question("fact",[h["paper_id"] for h in hits])
        self.assertNotIn("E99999",result["answer"]);self.assertEqual(len(result["evidence"]),15)


class WorkspaceQAIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_api_fulltext_grouping_judge_and_scope_snapshot(self):
        import httpx
        from app.main import app
        from app.schemas import PaperOut
        from app.services.papers import library,workspaces,conversations,documents
        from app.services.retrieval import hybrid,qa_index
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'PAPER_DB_PATH':str(Path(temp)/'papers.sqlite3')}):
            papers=[]
            workspaces.register('qa','QA')
            for i in range(3):
                p,_=library.save_paper(PaperOut(id=f'arxiv:2401.1234{i}',title=f'Paper {i}',abstract='Summary only.'),f'arxiv:2401.1234{i}')
                papers.append(p)
                workspaces.add('qa',p.local_id)
                documents.save(p.local_id,p.id,f'## Methods\n\nPaper {i} builds a graph and verifies retrieval evidence.','paper.pdf')
            async def encode(texts,**kw):return {t:[1.,0.] for t in texts}
            with patch.object(qa_index.embeddings,'embed_many',new=AsyncMock(side_effect=encode)):
                for _ in papers:await qa_index.run_one()
            selected=papers[:2];ids=[p.id for p in selected]
            chat=conversations.create('qa','New',[])
            async def model(messages):
                system=messages[0]['content'];body=json.loads(messages[1]['content'])
                if '检索规划器' in system:
                    self.assertEqual(body['selected_paper_ids'],ids)
                    return planned(ids).model_dump_json()
                if '只判断提供的证据' in system:return '{"status":"sufficient"}'
                if '独立评审' in system:
                    return json.dumps({'reviews':[{'claim_id':c['claim_id'],'verdict':'supported','evidence_ids':['E0001','E0002']} for c in body['claims']]})
                self.assertIn(ids[0],body['evidence']);self.assertIn(ids[1],body['evidence'])
                self.assertNotIn(papers[2].id,body['evidence'])
                return '{"answer":"两篇论文都核验检索证据 [E0001] [E0002]"}'
            async def ranked(q,docs,top_n):return [(i,.9) for i in range(min(top_n,len(docs)))]
            with patch.object(hybrid,'vector_search',new=AsyncMock(return_value=[])),patch.object(hybrid.rerank,'rerank',new=ranked),patch.object(att.llm,'complete',new=AsyncMock(side_effect=model)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    response=await client.post(f'/api/workspaces/qa/conversations/{chat["id"]}/messages',json={'question':'比较两篇论文的方法异同','paper_ids':[p.local_id for p in selected],'request_id':'comparison'})
            self.assertEqual(response.status_code,200)
            message=response.json();self.assertEqual(message['status'],'completed')
            self.assertEqual({e['paper_id'] for e in message['response']['evidence']},set(ids))
            self.assertTrue(all(e['basis']=='full_text' for e in message['response']['evidence']))
            self.assertEqual(message['response']['retrieval']['rounds'],1)
            self.assertIn('两篇论文',message['response']['answer'])
            self.assertEqual({p['paper_id'] for p in message['papers']},set(ids))
            self.assertEqual(conversations.get('qa',chat['id'])['messages'][0]['papers'],message['papers'])
