"""Verified public search examples. Results stay external until explicitly saved."""
import json
from pathlib import Path

RAG = [
    ('2310.11511','帮我找一篇按需检索、带自反思的 RAG 论文，工作区没有的。','Self-RAG 研究按需检索与自反思，符合自适应检索方向。'),
    ('2401.15884','找一篇关注检索结果出错后如何纠正的 RAG 论文。','CRAG 从检索结果可靠性和纠错角度改进检索增强生成。'),
    ('2310.01352','再找一篇同时优化检索器和语言模型的论文，不要重复刚才的。','RA-DIT 分别调整语言模型利用检索信息的能力，以及检索器返回相关内容的能力。'),
    ('2305.14283','继续找查询改写方向的论文。','这篇论文提出 Rewrite-Retrieve-Read，从改写检索查询切入。'),
    ('2310.11511','找按需检索与自反思方向的论文，先给我一篇。','Self-RAG 将按需检索与生成自反思结合起来。'),
    ('2305.06983','继续找生成过程中主动检索的论文。','Active Retrieval Augmented Generation 提出 FLARE，研究生成过程中的主动检索。'),
    ('2212.10496','再找用假想文档辅助零样本稠密检索的论文。','HyDE 利用假想文档的表示辅助零样本稠密检索。'),
    ('2310.01352','再补一篇检索器和生成模型联合适配的论文。','RA-DIT 的两个微调步骤分别面向语言模型与检索器。'),
    ('2305.14283','最后找一篇查询改写方向的，别重复前面四篇。','Query Rewriting 从搜索查询本身的适配切入，可作为另一条阅读线索。'),
]
HALLUCINATION = [
    ('2303.08896','找一篇黑盒大模型幻觉检测论文，不要工作区现有的。','SelfCheckGPT 研究黑盒条件下的幻觉检测，适合这一检索方向。'),
    ('2305.14251','找一篇长文本事实性评测论文。','FActScore 将长文本拆分为原子事实，评估其事实支持情况。'),
    ('2210.08726','再找一篇给生成内容补充依据并修订的论文。','RARR 研究为生成内容寻找依据并进行修订。'),
    ('2309.11495','再找生成后通过验证问题减少幻觉的论文。','Chain-of-Verification 通过规划和回答验证问题检查初始回答。'),
    ('2303.08896','找黑盒模型幻觉检测的论文，先给一篇。','SelfCheckGPT 是针对生成式大语言模型的黑盒幻觉检测方法。'),
    ('2305.14251','继续找细粒度事实性评测的论文。','FActScore 从原子事实层面评估长文本的事实精度。'),
    ('2210.08726','再找检索证据后修订输出的论文。','RARR 将证据归因与内容修订联系起来。'),
    ('2309.11495','再找用验证问题检查回答的论文。','Chain-of-Verification 研究通过验证步骤减少幻觉。'),
    ('2109.07958','最后找一篇研究模型模仿人类错误观念的评测基准论文。','TruthfulQA 衡量语言模型是否会模仿人类文本中的错误观念。'),
]

# Editorial examples grounded in the checked abstracts, not model execution logs.
REASONS = {
    '2310.11511': '这篇适合了解按需检索与自反思如何结合。摘要说明，Self-RAG 用同一模型按需获取材料，并通过反思标记检查检索段落和生成内容，而不是对所有问题固定检索。阅读时可重点区分“何时检索”和“如何评价生成内容”；这属于方法线索，不能据摘要推断它在所有任务上都能避免幻觉。',
    '2401.15884': '这篇对应检索结果不可靠时的纠错需求。CRAG 用轻量检索评估器判断文档质量，根据置信程度触发不同的知识获取动作，并引入网页搜索补充材料。它适合从检索质量控制角度阅读，与 Self-RAG 的自反思路线有所区别；摘要提供了机制概括，具体触发规则和效果仍需核对正文。',
    '2310.01352': '这篇适合研究检索器与生成模型之间如何适配。摘要描述两个微调步骤：一方面让语言模型更好地利用检索信息，另一方面让检索器返回更符合语言模型偏好的结果。它提供了训练适配方向的阅读线索，与查询改写、检索后纠错不同；不能把双步骤微调直接等同于所有设置下的端到端联合训练。',
    '2305.14283': '这篇对应通过查询改写改进检索的方向。摘要提出 Rewrite-Retrieve-Read 框架，先生成适合检索的查询，再获取材料供阅读器使用，并探索利用阅读器反馈训练改写器。阅读重点是用户问题与检索查询之间的适配，与直接调整检索器或生成模型不同；具体训练设置与实验收益需要查看正文。',
    '2305.06983': '这篇适合了解长文本生成过程中何时主动检索。FLARE 根据即将生成的句子预测后续内容，将其作为查询；遇到低置信词时检索相关文档并重新生成句子。它关注生成过程中的持续取证，而不是只在开始前检索一次，可与 Self-RAG 对照阅读；摘要不足以确定你的模型中应使用的置信阈值。',
    '2212.10496': '这篇对应假想文档辅助零样本稠密检索的需求。HyDE 先让语言模型生成假想文档，再用编码器把它映射到向量空间，检索真实语料中的相近文档。它适合从查询表示角度理解检索改进；假想文档可能含错误细节，不能将生成内容本身当作事实证据。',
    '2303.08896': '这篇适合黑盒条件下的幻觉检测研究。SelfCheckGPT 利用同一模型多次采样回答之间的事实一致性进行检查，不依赖模型内部输出概率或外部事实数据库。它提供了基于采样一致性的检测线索，可从句子检测和篇章事实性两个层面阅读；一致性检查本身并不等于取得外部事实证据。',
    '2305.14251': '这篇适合长文本的细粒度事实性评测。FActScore 将生成文本拆分为原子事实，计算其中有可靠知识来源支持的比例，避免把混有正确与错误信息的整段文本只判成对或错。它适合作为评估输出事实精度的阅读起点，与检测或修订方法互补；具体评测设置需要核对正文。',
    '2210.08726': '这篇对应为已有生成内容补充依据并修订的需求。RARR 先为模型输出寻找可归因的材料，再修订缺乏支持的部分，同时尽量保留原有内容。它关注生成后的取证与编辑，可与生成前检索或事实性评分方法区分阅读；摘要不能证明所有修订后的陈述都已得到充分支持。',
    '2309.11495': '这篇适合了解通过验证问题检查初始回答的路线。CoVe 先起草回答，再规划验证问题、独立回答这些问题，最后生成经过验证的回答，以减少验证过程被初稿带偏。它提供了多步骤自检的阅读角度，与检索外部材料后修订的 RARR 不同；该流程不应被理解为正确性保证。',
    '2109.07958': '这篇适合从评测基准角度研究模型为何复述错误观念。TruthfulQA 围绕人类常见误解构造问题，衡量模型是否会生成从人类文本中学到的错误回答。它可作为检验真实性的阅读线索，与生成过程中的检测和缓解方法分工不同；基准结果不能直接代表所有真实应用场景。',
}


def _untouched(chat, title, examples):
    turns = chat.get('turns', [])
    return chat.get('title') == title and len(turns) == len(examples) and all(
        t.get('demo_example') and t.get('question') == q and t.get('task', {}).get('answer', '') in (a, a+' 论文见下方，可查看详情或收藏到工作区。')
        for t, (_, q, a) in zip(turns, examples))


def install(db, workspace_id, is_rag, members, legacy_examples, millis, stamp):
    dataset=json.loads(Path(__file__).with_name('demo_discovery.json').read_text(encoding='utf-8'))
    papers={p['id']:p for p in dataset['papers']}
    examples=RAG if is_rag else HALLUCINATION
    for index,(start,count) in enumerate(((0,1),(1,3),(4,5))):
        cid=f'demo-reviewed-{index+1}'
        row=db.execute('SELECT payload,version,deleted FROM assistant_chats WHERE workspace_id=? AND id=?',(workspace_id,cid)).fetchone()
        if row:
            old=json.loads(row['payload'])
            original_title=f'示例 · {("阅读起点","方法解读","证据边界")[index]} · {count} 轮'
            discovery_title=f'示例 · {("主题检索","扩展方向","逐步找论文")[index]} · {count} 轮'
            untouched=_untouched(old, original_title, legacy_examples[start:start+count]) or _untouched(old, discovery_title, examples[start:start+count])
            if row['deleted'] or not untouched:
                continue # Never replace a user's own edits, continuation or deletion.
        turns=[]
        for offset,(ref,question,reason) in enumerate(examples[start:start+count]):
            paper=papers['arxiv:'+ref]
            if paper['id'] in members:
                continue # Initial examples intentionally discover papers outside the workspace.
            turns.append({'id':millis+index*100+offset,'answeredAt':millis+index*100+offset+1,
                          'question':question,'demo_example':True,
                          'task':{'task_id':f'{cid}-{offset}','status':'done','events':[],
                                  'answer':'推荐这篇作为阅读线索，具体理由见下方。以下说明依据公开摘要整理。',
                                  'output_mode':'bibliography','result_limit':1,'recommended':[paper],
                                  'recommendation_reasons':{paper['id']:REASONS[ref]},
                                  'demo_provenance':{'source':dataset['source'],'checked_at':dataset['checked_at']}}})
        if index == 0 and turns:
            refs = ('2401.15884', '2310.01352', '2305.14283') if is_rag else ('2305.14251', '2210.08726', '2309.11495')
            batch = [papers['arxiv:'+ref] for ref in refs if 'arxiv:'+ref not in members]
            question = ('再一次性找 3 篇改进 RAG 检索流程的论文，分别从检索纠错、模型与检索器适配、查询改写三个方向推荐，不要重复刚才那篇。' if is_rag else
                        '再一次性找 3 篇事实性评估与幻觉缓解论文，分别关注细粒度评测、依据检索后修订、验证问题自检，不要重复刚才那篇。')
            turns.append({'id':millis+10,'answeredAt':millis+11,'question':question,'demo_example':True,
                          'task':{'task_id':f'{cid}-batch','status':'done','events':[],
                                  'answer':f'这里推荐 {len(batch)} 篇不同路线的论文，逐篇理由见下方。以下说明依据公开摘要整理，未比较全文实验结果。',
                                  'output_mode':'bibliography','result_limit':3,'recommended':batch,
                                  'recommendation_reasons':{p['id']:REASONS[p['id'].split(':',1)[1]] for p in batch},
                                  'demo_provenance':{'source':dataset['source'],'checked_at':dataset['checked_at']}}})
        if not turns:continue
        chat={'id':cid,'title':f'示例 · {("主题检索","扩展方向","逐步找论文")[index]} · {len(turns)} 轮',
              'titleEdited':True,'createdAt':millis-index,'updatedAt':millis-index,'selectedIds':[],'turns':turns}
        raw=json.dumps(chat,ensure_ascii=False)
        if row:
            db.execute('UPDATE assistant_chats SET payload=?,version=version+1,updated_at=? WHERE workspace_id=? AND id=?',(raw,stamp,workspace_id,cid))
        else:db.execute('INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)',(workspace_id,cid,raw,1,stamp))
