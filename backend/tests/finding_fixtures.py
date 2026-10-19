import json

PLAN = {'resolved_question':'找三篇关于文本向量模型的论文','target_count':3,
        'core_topics':['text embedding models'],'constraints':{'year_from':None,'year_to':None,'temporal':None,'requirements':[]},
        'exclusions':[],'queries':['text embedding models','sentence embeddings'],
        'needs_clarification':False,'clarification_question':None,'interpretation':'我会查找文本向量表示模型的论文。'}
CANDIDATES = [
    {'id':'arxiv:1','title':'Unsupervised Contrastive Learning of Sentence Representations','abstract':'A pretrained encoder represents sentences as dense vectors.','year':2021,'sources':['arxiv']},
    {'id':'arxiv:2','title':'General-purpose Representations','abstract':'Training a text embedding model for multiple tasks.','year':2023,'sources':['arxiv']},
    {'id':'arxiv:3','title':'Multilingual Encoders','abstract':'A text embedding model for cross-lingual retrieval.','year':2024,'sources':['arxiv']},
    {'id':'arxiv:4','title':'A query suggestion workflow for life science IR-systems','abstract':'Interactive query suggestions for biological databases.','year':2014,'sources':['arxiv']},
]

def selection(count=4, accepted=(0,1,2)):
    return {'judgments':[{'index':i,'relevant':i in accepted,'score':9 if i in accepted else 2,
                         'reason':'摘要说明它通过训练编码器产生句子向量，适合文本表示模型需求。' if i in accepted else '仅研究生命科学查询建议，与文本向量模型训练无关。'} for i in range(count)],
            'summary':'根据标题摘要筛选相关模型论文。','next_query':None}

def search_result(papers=None, errors=None):
    rows=CANDIDATES if papers is None else papers
    return {'query':'text embedding models','page':1,'page_size':20,'total':len(rows),'total_estimated':False,
            'papers':rows,'source_counts':{'arxiv':len(rows)},'source_errors':errors or {}}

def dumps(value):
    return json.dumps(value,ensure_ascii=False)
