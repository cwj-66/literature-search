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
            old_turns=old.get('turns',[])
            untouched=old.get('title')==original_title and len(old_turns)==count and all(
                t.get('demo_example') and t.get('question')==q and t.get('task',{}).get('answer','').startswith(a)
                for t,(_,q,a) in zip(old_turns,legacy_examples[start:start+count]))
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
                                  'answer':reason+' 论文见下方，可查看详情或收藏到工作区。',
                                  'output_mode':'bibliography','result_limit':1,'recommended':[paper],
                                  'recommendation_reasons':{paper['id']:reason},
                                  'demo_provenance':{'source':dataset['source'],'checked_at':dataset['checked_at']}}})
        if not turns:continue
        chat={'id':cid,'title':f'示例 · {("主题检索","扩展方向","逐步找论文")[index]} · {len(turns)} 轮',
              'titleEdited':True,'createdAt':millis-index,'updatedAt':millis-index,'selectedIds':[],'turns':turns}
        raw=json.dumps(chat,ensure_ascii=False)
        if row:
            db.execute('UPDATE assistant_chats SET payload=?,version=version+1,updated_at=? WHERE workspace_id=? AND id=?',(raw,stamp,workspace_id,cid))
        else:db.execute('INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)',(workspace_id,cid,raw,1,stamp))
