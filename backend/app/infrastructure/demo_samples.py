"""Reviewed examples, never fabricated model runs or real user history.

Facts and short quotations were checked against the public arXiv abstracts on
2026-10-22. No web search, model call or paid operation runs during installation.
Only the two original demo workspaces and their existing papers are eligible.
"""
import json
from datetime import datetime, timezone

VERSION = "discovery-examples-20261022-v2"
NOTICE = "预置演示：依据公开论文摘要整理，非真实用户记录或现场模型输出。未核对全文实验数字；继续提问将使用正常研究流程。"

SOURCES = {
    "arxiv:2407.02485": {
        "name": "RankRAG", "field": ("信息检索", "Information Retrieval"),
        "quote": "instruction-tunes a single LLM for the dual purpose of context ranking and answer generation in RAG",
    },
    "arxiv:2401.15391": {
        "name": "MultiHop-RAG", "field": ("多跳问答", "Multi-Hop Question Answering"),
        "quote": "multi-hop queries, which require retrieving and reasoning over multiple pieces of supporting evidence",
    },
    "arxiv:2409.11353": {
        "name": "THaMES", "field": ("大语言模型幻觉缓解", "Hallucination Mitigation in Large Language Models"),
        "quote": "featuring automated test set generation, multifaceted benchmarking, and adaptable mitigation strategies",
    },
    "arxiv:2503.21676": {
        "name": "事实学习动力学", "field": ("语言模型知识获取", "Knowledge Acquisition in Language Models"),
        "quote": "hallucinations emerge simultaneously with knowledge",
    },
}

# Each answer has a specific paper and an exact abstract quotation available in UI.
RAG = [
    ("arxiv:2407.02485", "找一篇把排序和生成结合起来的 RAG 论文。", "可以看工作区中的 RankRAG。作者用一个大模型同时承担上下文排序和答案生成，适合从检索后的上下文筛选切入。"),
    ("arxiv:2407.02485", "RankRAG 的两个任务分别是什么？", "它把上下文排序与答案生成放到同一个经过指令微调的大模型中。这里说的是论文提出的框架，不能推断每一种 RAG 都需要这样训练。"),
    ("arxiv:2401.15391", "哪篇论文适合了解多跳检索问题？", "MultiHop-RAG 研究需要多条支持证据的多跳问题，提供相应基准。它关注跨证据检索和推理，不是单纯比较生成答案的语言流畅度。"),
    ("arxiv:2401.15391", "多跳问题为什么不能只找一段材料？", "该论文把多跳问题定义为需要检索并对多条支持证据进行推理的问题。因此，单段相关文本不一定覆盖回答所需的全部证据。"),
    ("arxiv:2407.02485", "给我一条从排序角度阅读 RAG 的线索。", "先读 RankRAG，区分上下文排序和答案生成这两个任务，再检查二者如何在同一模型中结合。当前摘要只能支持框架概括。"),
    ("arxiv:2401.15391", "给我一条从评测角度阅读 RAG 的线索。", "再读 MultiHop-RAG，重点看多条支持证据如何共同服务于问题回答。摘要说明其研究对象是多跳查询；具体数据规模应查看论文正文。"),
    ("arxiv:2407.02485", "RankRAG 一定能解决所有 RAG 错误吗？", "不能这样下结论。摘要支持它统一排序与生成的设计，不能证明对所有领域、所有数据和所有错误类型都有效。"),
    ("arxiv:2401.15391", "检索到了相关文本，就等于多跳问答正确了吗？", "不等于。多跳问题还要求对多条支持证据进行推理；相关文本的出现本身不能替代答案正确性的核验。"),
    ("arxiv:2407.02485", "现在能给出精确提升百分比吗？", "这份示例只核对了公开摘要中的方法描述，没有核对正文表格、基线和评测设置，因此不填写精确提升百分比。"),
]
HALLUCINATION = [
    ("arxiv:2409.11353", "找一篇能了解幻觉评测和缓解流程的论文。", "工作区中的 THaMES 将自动测试集构建、多角度评测和可调整的缓解策略整合到同一框架，可作为流程层面的阅读起点。"),
    ("arxiv:2409.11353", "THaMES 只是一个检测指标吗？", "不是。摘要描述的是包含测试集生成、评测和缓解策略的集成框架，不能把它等同于某一个单独指标。"),
    ("arxiv:2503.21676", "还有哪篇适合从知识学习角度理解幻觉？", "How do language models learn facts? 从语言模型学习事实的动力学切入。它在研究设置中观察到知识与幻觉同时出现。"),
    ("arxiv:2503.21676", "知识增长能保证幻觉完全消失吗？", "这篇论文报告的观察不支持这种保证：在其研究设置中，幻觉随知识共同出现。不能把这一观察直接推广成所有真实模型的定量规律。"),
    ("arxiv:2409.11353", "如何组织一次幻觉方向的阅读？", "可以先用 THaMES 理解测试集、评测与缓解策略之间的关系。示例没有把某一种策略描述成对所有模型都最优。"),
    ("arxiv:2503.21676", "再补充哪种阅读视角？", "再看事实学习动力学论文，关注知识习得过程中幻觉何时出现。它与工程评测框架解决的是不同层次的问题。"),
    ("arxiv:2409.11353", "这份示例能证明系统已经消除了幻觉吗？", "不能。展示一篇缓解和评测框架，只说明可以研究这些环节，不代表当前系统已经完成全部实验，更不代表幻觉被消除。"),
    ("arxiv:2503.21676", "摘要里的观察可以直接当作产品承诺吗？", "不可以。知识和幻觉共同出现是论文研究设置中的观察，需要结合其任务、训练条件和实验范围理解。"),
    ("arxiv:2409.11353", "可以展示未经核对的准确率吗？", "不可以。这里没有核对全文中的实验表格、评测任务和基线，因此只展示有摘要依据的方法范围，不补写准确率。"),
]


def install():
    """Idempotent, additive migration; never replace visitors' own chats or papers."""
    from app.infrastructure.db import connection
    from app.assistant_chats import ensure
    from app.schemas import ResearchField
    from app.services.research_fields import store
    from app.infrastructure import demo_discovery
    stamp = datetime.now(timezone.utc).isoformat()
    millis = int(datetime.now(timezone.utc).timestamp() * 1000)
    with connection() as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)")
        ensure(db)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM demo_sample_updates WHERE version=?", (VERSION,)).fetchone():
            return
        for workspace_id, name in db.execute("SELECT id,name FROM workspaces WHERE id IN ('pmvcf313hr4xt','pmvci88eig1qi')").fetchall():
            members = {json.loads(r['data'])['id']: {**json.loads(r['data']), 'local_id':r['local_id']}
                       for r in db.execute('SELECT p.* FROM papers p JOIN workspace_papers wp ON wp.local_id=p.local_id WHERE wp.workspace_id=?', (workspace_id,))}
            examples = RAG if name == 'RAG研究' else HALLUCINATION
            if not all(ref in members for ref, _, _ in examples):
                continue  # A visitor may have removed a sample; never silently restore it.
            for ref in {e[0] for e in examples}:
                source=SOURCES[ref]
                fields=[source['field']]
                if examples is RAG: fields.append(('检索增强生成','Retrieval-Augmented Generation'))
                for label, canonical in fields:
                    payload=ResearchField(name=label,canonical_name=canonical).model_dump()
                    payload.update({'demo_provenance':{'kind':'reviewed_abstract','url':'https://arxiv.org/abs/'+ref.split(':',1)[1], 'quote':source['quote']}})
                    fid=store.register(ResearchField(name=label,canonical_name=canonical))
                    db.execute('INSERT OR IGNORE INTO paper_fields VALUES(?,?,?)',(members[ref]['local_id'],fid,json.dumps(payload,ensure_ascii=False)))
            demo_discovery.install(db, workspace_id, examples is RAG, members, examples, millis, stamp)
            for index, (start, count) in enumerate(((0,1),(1,3),(4,5))):
                title=f'示例 · {("阅读起点","方法解读","证据边界")[index]} · {count} 轮'
                selected=list(dict.fromkeys(members[e[0]]['local_id'] for e in examples[start:start+count]))
                cid=f'demo-reviewed-{index+1}'
                db.execute('INSERT OR IGNORE INTO research_conversations VALUES(?,?,?,?,?,?)',(cid+'-'+workspace_id,workspace_id,title,json.dumps(selected),stamp,stamp))
                for offset,(ref,question,answer) in enumerate(examples[start:start+count]):
                    paper=members[ref]; mid=f'{cid}-{offset}'
                    evidence={'evidence_id':'E0001','paper_id':ref,'quote':SOURCES[ref]['quote'],'section':'公开摘要 · 预置演示','material':'original'}
                    response={'answer':answer+' [E0001]','evidence':[evidence],'reviews':[],'coverage_gap':False}
                    message={'id':mid,'question':question,'papers':[{'id':paper['local_id'],'paper_id':ref,'title':paper['title']}],
                             'status':'completed','created_at':stamp,'response':response,'error':None,'demo_example':True}
                    db.execute('INSERT OR IGNORE INTO research_messages VALUES(?,?,?)',(mid,cid+'-'+workspace_id,json.dumps(message,ensure_ascii=False)))
        db.execute('INSERT INTO demo_sample_updates VALUES(?)',(VERSION,))
