import json
import unittest
from unittest.mock import AsyncMock,patch
from pydantic import ValidationError
from app.services.retrieval import qa_workflow as qa,research_policy as policy
from app.services.analysis import attribution as att


def evidence():
    return [{'paper_id':'p1','chunk_id':'child1','text':'The model ranks contexts before answering. Only English question answering was evaluated.',
             'basis':'full_text','section':'Methods'}]


def statement(category='analysis',**updates):
    return {'category':category,'text':'该评估范围不足以保证多语言泛化 [E0001]',
            'evidence_ids':['E0001'],'reasoning':'评估只覆盖英文问答，未覆盖跨语言分布。',
            'uncertainty':'这是助手分析；缺少多语言结果不等于一定失败。',**updates}


class ResearchIntentTests(unittest.IsolatedAsyncioTestCase):
    def test_six_research_profiles(self):
        cases=[('作者为什么研究这个问题，现有方法有什么不足','motivation','understand'),
               ('这个方法具体怎么做，为什么这样设计','method','understand'),
               ('实验如何验证观点，结果能证明什么','experiments','understand'),
               ('这些发现放到其他场景还站得住脚吗','critique','challenge'),
               ('比较这几篇论文的共同点、差异和观点冲突','comparison','compare'),
               ('两种方法能否结合，有什么后续研究方向','exploration','explore')]
        for q,intent,mode in cases:
            with self.subTest(intent=intent):
                plan=qa.fallback(q,['p1','p2'])
                self.assertEqual(plan.research_intent,intent);self.assertEqual(plan.reasoning_mode,mode)
                self.assertTrue(plan.evidence_needs)

    async def test_semantic_planning_handles_open_request_without_fixed_keywords(self):
        q='现有工作还有哪些角度值得重新想一遍'
        plan=qa.Plan(resolved_question=q,queries=['method assumptions','evaluation scope'],paper_ids=['p1'],
                     research_intent='exploration',evidence_needs=['method','limitation'])
        with patch.object(qa.llm,'complete',new=AsyncMock(return_value=plan.model_dump_json())) as model:
            result=await qa.plan_question(q,['p1'],[],qa.Budget())
        self.assertEqual(model.await_count,1)
        self.assertEqual(result.reasoning_mode,'explore');self.assertNotEqual(result.question_type,'fact')

    async def test_simple_question_still_avoids_planning(self):
        with patch.object(qa.llm,'complete',new=AsyncMock()) as model:
            plan=await qa.plan_question('研究目标是什么',['p1'],[],qa.Budget())
        model.assert_not_awaited();self.assertEqual(plan.strategy,'direct')

    async def test_coverage_does_not_demand_prior_proof_of_a_new_proposal(self):
        plan=qa.fallback('两种方法能否结合',['p1','p2'])
        async def complete(system,payload,timeout):
            self.assertIn('不能要求论文已经做过',system)
            self.assertEqual(payload['plan']['research_intent'],'exploration')
            return {'status':'sufficient'}
        with patch.object(qa,'complete_json',new=complete):
            result=await qa.assess(plan,'能否结合',['p1','p2'],[*evidence(),{**evidence()[0],'paper_id':'p2'}],qa.Budget())
        self.assertEqual(result.status,'sufficient')

    async def test_english_coverage_note_is_auditable_but_visible_limitation_is_chinese(self):
        plan=qa.fallback('如何评价研究边界',['p1'])
        coverage=qa.Coverage(status='partial',note='Detailed method evidence is missing.')
        with patch.object(qa,'plan_question',new=AsyncMock(return_value=plan)), \
             patch.object(qa,'assess',new=AsyncMock(return_value=coverage)), \
             patch('app.services.retrieval.qa_index.statuses',return_value={}), \
             patch.object(qa.rerank,'rerank',new=AsyncMock(side_effect=RuntimeError('offline'))):
            _,meta=await qa.retrieve_evidence('如何评价研究边界',['p1'],[],AsyncMock(return_value=evidence()))
        self.assertEqual(meta['coverage']['note'],coverage.note)
        self.assertTrue(any('当前证据未完整覆盖' in n for n in meta['limitations']))
        self.assertNotIn(coverage.note,meta['limitations'])

    async def test_freeform_chinese_diagnostic_is_not_published_as_a_paper_fact(self):
        plan=qa.fallback('两种方法能否结合',['p1'])
        coverage=qa.Coverage(status='partial',note='作者从未做过组合实验，因此完全无法分析。')
        with patch.object(qa,'plan_question',new=AsyncMock(return_value=plan)),patch.object(qa,'assess',new=AsyncMock(return_value=coverage)),patch('app.services.retrieval.qa_index.statuses',return_value={}),patch.object(qa.rerank,'rerank',new=AsyncMock(side_effect=RuntimeError('offline'))):
            _,meta=await qa.retrieve_evidence('能否结合',['p1'],[],AsyncMock(return_value=evidence()))
        self.assertEqual(meta['coverage']['note'],coverage.note)
        self.assertNotIn(coverage.note,meta['limitations'])
        self.assertTrue(any('当前证据未完整覆盖' in note for note in meta['limitations']))


class ResearchAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def answer(self,payload,judge=None,intent='critique'):
        async def approved(q,claims,ev):
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported',evidence_ids=['E0001']) for c in claims]
        meta={'plan':{'research_intent':intent,'question_type':'synthesis'}}
        with patch.object(qa,'retrieve_evidence',new=AsyncMock(return_value=(evidence(),meta))), \
             patch.object(att.llm,'complete',new=AsyncMock(return_value=json.dumps(payload))), \
             patch.object(att,'compose_reviewed_answer',new=AsyncMock(return_value=None)), \
             patch.object(att,'judge_claims',new=judge or approved):
            return await att.answer_question('评价适用边界',['p1'])

    async def test_grounded_analysis_need_not_be_authors_explicit_limitation(self):
        payload={'answer':'【助手分析】尚不能保证多语言泛化，这不等于已证明失败 [E0001]','claims':[statement()]}
        async def judge(q,claims,ev):
            analysis=claims[0]['claim']
            self.assertEqual(analysis.category,'analysis');self.assertTrue(analysis.reasoning)
            self.assertIn('未覆盖',analysis.reasoning)
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported',evidence_ids=['E0001']) for c in claims]
        result=await self.answer(payload,judge)
        self.assertNotIn('【助手分析】',result['answer'])
        self.assertIn('缺少多语言结果不等于一定失败',result['answer'])
        self.assertEqual(result['statements'][0]['category'],'analysis')
        self.assertNotIn('作者承认',result['answer'])

    async def test_unverified_hypothesis_is_retained_with_test_plan(self):
        s=statement('hypothesis',text='可尝试将上下文排序用于冲突调和前的筛选 [E0001]',
            reasoning='所选方法具备上下文排序能力，可作为前置筛选模块。',
            uncertainty='尚未验证；可能丢失反例上下文。',validation='对照无排序、只排序与组合方案，固定预算并测忠实性和召回。')
        result=await self.answer({'answer':'【待验证研究设想】可以尝试组合，但尚未证实 [E0001]','claims':[s]},intent='exploration')
        self.assertIn('尚未验证',result['answer'])
        self.assertEqual(result['statements'][0]['validation'],s['validation'])

    async def test_missing_grounding_cannot_be_approved_by_whole_answer(self):
        result=await self.answer({'answer':'组合后一定提升99% [E0001]','claims':[{'category':'hypothesis','text':'组合后一定提升99%','evidence_ids':['E0001']}]},intent='exploration')
        self.assertNotIn('提升99%',result['answer']);self.assertEqual(result['statements'],[])

    async def test_natural_analysis_does_not_require_literal_labels(self):
        text='现有评估不足以保证多语言泛化，但也不能据此认定其他语言一定失败。[E0001]'
        result=await self.answer({'answer':text,'claims':[statement()]})
        self.assertEqual(result['answer'],text)
        self.assertEqual(result['statements'][0]['category'],'analysis')

    async def test_fallback_preserves_uncertainty_without_audit_form(self):
        async def judge(q,claims,ev):
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported' if c['claim'].category!='mixed' else 'insufficient',evidence_ids=['E0001']) for c in claims]
        result=await self.answer({'answer':'该方法不能泛化 [E0001]','claims':[statement()]},judge)
        self.assertNotIn('【助手分析】',result['answer'])
        self.assertNotIn('推理依据：',result['answer'])
        self.assertIn('缺少多语言结果不等于一定失败',result['answer'])

    async def test_unclassified_analysis_cannot_hide_in_an_approved_whole_answer(self):
        author={'category':'author_claim','text':'作者仅报告英文评估 [E0001]','evidence_ids':['E0001']}
        payload={'answer':'作者仅报告英文评估。[E0001]\n【助手分析】所有其他语言都会失败。[E0001]','claims':[author]}
        result=await self.answer(payload,intent='motivation')
        self.assertNotIn('都会失败',result['answer']);self.assertIn('作者仅报告',result['answer'])

    async def test_rejected_universal_claim_removed_without_erasing_valid_analysis(self):
        safe=statement()
        fabricated=statement(text='该模型在所有语言上都失败 [E0001]')
        async def judge(q,claims,ev):
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported' if c['claim'].text==safe['text'] else 'insufficient',evidence_ids=['E0001']) for c in claims]
        result=await self.answer({'answer':'【助手分析】所有语言都失败 [E0001]','claims':[safe,fabricated]},judge)
        self.assertNotIn('所有语言',result['answer']);self.assertIn('不足以保证',result['answer'])

    def test_hypothesis_and_results_require_different_review_fields(self):
        with self.assertRaises(ValidationError):policy.ResearchStatement.model_validate(statement('hypothesis'))
        with self.assertRaises(ValidationError):policy.ResearchStatement.model_validate(statement('experimental_result'))
        self.assertTrue(policy.ResearchStatement.model_validate(statement('experimental_result',conditions='仅英文问答基准')))

    async def test_judge_receives_categories_and_reasoning_not_just_quote_matching(self):
        claim=att.AnswerClaim(kind='main_content',text='可能不泛化',category='analysis',reasoning='仅评估英文',uncertainty='待检验',premise_evidence_ids=['E0001'])
        raw='{"reviews":[{"claim_id":"A0001","verdict":"supported","evidence_ids":["E0001"]}]}'
        with patch.object(att.llm,'complete',new=AsyncMock(return_value=raw)) as model:
            await att.judge_claims('评估边界',att.bind_claim_ids([claim]),[att.EvidenceItem(evidence_id='E0001',paper_id='p1',quote='Only English was evaluated.')])
        messages=model.call_args.args[0];body=json.loads(messages[1]['content'])
        self.assertEqual(body['claims'][0]['category'],'analysis')
        self.assertEqual(body['claims'][0]['reasoning'],'仅评估英文')
        self.assertIn('不因论文未明确说出该推论就拒绝',messages[0]['content'])


class ReadableCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_supported_rewrite_with_scoped_citations_is_released(self):
        source=att.EvidenceItem(evidence_id='E0001',paper_id='p1',quote=evidence()[0]['text'])
        text='评估只覆盖英文，因此多语言泛化仍需验证；这不意味着一定失败。[E0001]'
        async def judge(q,claims,ev):
            self.assertEqual({e.paper_id for e in ev},{'p1'})
            self.assertEqual(claims[-1]['claim'].text,text)
            self.assertTrue(claims[0]['claim'].uncertainty)
            return [att.JudgeItem(claim_id=c['claim_id'],verdict='supported',evidence_ids=['E0001']) for c in claims]
        with patch.object(att.llm,'complete',new=AsyncMock(return_value=json.dumps({'answer':text}))),patch.object(att,'judge_claims',new=judge):
            result=await att.compose_reviewed_answer('局限性',[statement()],[source])
        self.assertEqual(result['answer'],text)

    async def test_failed_judge_invalid_ids_or_missing_paper_never_publish_rewrite(self):
        sources=[att.EvidenceItem(evidence_id=f'E000{i}',paper_id=f'p{i}',quote='Real evidence text') for i in (1,2)]
        ss=[statement(),statement('author_claim',text='另一篇的方法',evidence_ids=['E0002'])]
        for text in ['全新论断 [E9999]','遗漏另一篇 [E0001]','未经复核的文本 [E0001] [E0002]']:
            with self.subTest(text=text),patch.object(att.llm,'complete',new=AsyncMock(return_value=json.dumps({'answer':text}))),patch.object(att,'judge_claims',new=AsyncMock(return_value=[])):
                self.assertIsNone(await att.compose_reviewed_answer('比较',ss,sources))

    def test_safe_fallback_marks_unverified_proposal_and_keeps_test_plan(self):
        s=policy.ResearchStatement.model_validate(statement('hypothesis',validation='固定其他条件比较两种设置。'))
        text=att.render_research_statement(s,['E0001'])
        self.assertIn('尚待验证',text);self.assertIn('固定其他条件',text)
        self.assertNotIn('【',text);self.assertNotIn('推理依据：',text)
