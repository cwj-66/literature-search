"""Bounded two-round API discovery, quote validation and semantic report review."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Literal

from pydantic import BaseModel, Field
from app.infrastructure import llm
from app.schemas import PaperOut
from app.services.papers.paper_merge import identity_keys, combine
from app.services.retrieval import search
from app.services.research_fields import prompts, store

log = logging.getLogger(__name__)
ABSTRACT_CHARS = 1600

class Query(BaseModel):
    query: str = Field(min_length=2, max_length=250)
    purpose: Literal['foundation','breakthrough','recent','survey']
class Plan(BaseModel):
    queries: list[Query] = Field(default_factory=list, max_length=4)
class Evidence(BaseModel):
    paper_id: str = Field(max_length=500)
    quote: str = Field(min_length=12, max_length=900)
class Milestone(BaseModel):
    paper_id: str = Field(max_length=500)
    kind: Literal['foundation','breakthrough','representative']
    contribution: str = Field(min_length=5, max_length=1000)
    significance: str = Field(min_length=5, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1,max_length=4)
class Observation(BaseModel):
    title: str = Field(min_length=2,max_length=120)
    description: str = Field(min_length=5,max_length=1500)
    evidence: list[Evidence] = Field(min_length=1,max_length=4)
class Direction(Observation):
    uncertainty: str = Field(min_length=5,max_length=1000)
class Draft(BaseModel):
    summary: str = Field(min_length=5,max_length=2000)
    milestones: list[Milestone] = Field(default_factory=list,max_length=15)
    hotspots: list[Observation] = Field(default_factory=list,max_length=4)
    directions: list[Direction] = Field(default_factory=list,max_length=4)
    limitations: list[str] = Field(default_factory=list,max_length=10)
class Review(BaseModel):
    accepted: list[int] = Field(default_factory=list,max_length=23)

async def structured(system, material, schema):
    # The provider requires the literal word JSON when json_object mode is used.
    messages=[{'role':'system','content':system+'\n只输出合法 JSON 对象。'},{'role':'user','content':json.dumps(material,ensure_ascii=False)}]
    for attempt in range(2):
        raw=await llm.complete(messages)
        try:
            text=raw.strip()
            if text.startswith('```'):
                text='\n'.join(text.splitlines()[1:-1])
            return schema.model_validate_json(text)
        except ValueError as exc:
            if attempt:
                raise llm.AnalysisError('领域分析的结构化结果无法解析') from exc
            messages.extend([{'role':'assistant','content':raw[:12000]}, {'role':'user','content':'仅修正JSON结构，不新增论文或事实。结构错误：'+str(exc)[:700]}])

def add_candidates(pool, papers):
    for raw in papers:
        paper=raw if isinstance(raw,PaperOut) else PaperOut.model_validate(raw)
        if not paper.abstract or not paper.year:
            continue
        keys=identity_keys(paper)|{paper.id}
        old=next((p for p in pool if keys & (identity_keys(p)|{p.id})),None)
        if old:
            merged=combine(old,paper)
            pool[pool.index(old)]=merged
        elif len(pool)<96:
            pool.append(paper)

def material(pool):
    # The only text entering the shared report is actual public API metadata.
    return [{'id':p.id,'title':p.title,'year':p.year,'citation_count':p.citation_count,
             'abstract':(p.abstract or '')[:ABSTRACT_CHARS]} for p in pool]

def valid_evidence(items, by_id, recent_year=None):
    def normalize(s): return ' '.join(s.split())
    found=[]
    for item in items:
        paper=by_id.get(item.paper_id)
        if not paper or recent_year and (paper.year or 0)<recent_year:
            continue
        # Quotes must occur in the actual snippet provided to the model.
        if normalize(item.quote) not in normalize((paper.abstract or '')[:ABSTRACT_CHARS]):
            continue
        found.append(item.model_dump())
    return found

def ground(draft, pool, end_year):
    by_id={p.id:p for p in pool}
    rows=[]
    seen=set()
    for point in draft.milestones:
        if point.paper_id not in by_id or point.paper_id in seen:
            continue
        evidence=valid_evidence([e for e in point.evidence if e.paper_id==point.paper_id],by_id)
        if evidence:
            seen.add(point.paper_id)
            rows.append({'section':'milestones',**point.model_dump(),'evidence':evidence,'year':by_id[point.paper_id].year})
    for section in ('hotspots','directions'):
        for entry in getattr(draft,section):
            evidence=valid_evidence(entry.evidence,by_id,end_year-2)
            if evidence:
                rows.append({'section':section,**entry.model_dump(),'evidence':evidence})
    return rows

async def discover(field, job=None):
    end=datetime.now(timezone.utc).year
    async def progress(message):
        if job: await asyncio.to_thread(store.stage,job,message)
    await progress('规划早期、突破与近期检索')
    plan=await structured(prompts.PLAN,{'field':field['canonical_name'],'name':field['name'],'as_of':end},Plan)
    # Always include both temporal ends even if the model plans only recent work.
    queries=[Query(query=field['canonical_name'],purpose='foundation'),Query(query=field['canonical_name'],purpose='recent'),*plan.queries]
    pool=[]; priority=[]; log_queries=[]; errors=[]; seen=set()
    async def retrieve(query, temporal_anchor=False):
        key=(query.query.casefold(),query.purpose)
        if key in seen:return
        seen.add(key)
        # Title-based seminal discovery needs relevance; date order can bury the
        # exact paper under older papers sharing some of the same vocabulary.
        sort='date_asc' if temporal_anchor else 'date' if query.purpose=='recent' else 'relevance'
        lower=end-2 if query.purpose=='recent' else 1900
        # Each request uses both configured sources and their existing retry/limiting.
        try:
            result=await asyncio.wait_for(search.search_papers(query.query,1,12,sort,'all',lower,end),timeout=100)
            add_candidates(pool,result.get('papers',[]))
            if query.purpose in {'foundation','breakthrough'} and not temporal_anchor:
                for p in result.get('papers',[])[:2]:
                    keys=identity_keys(p)|{p.id}
                    match=next((q for q in pool if keys & (identity_keys(q)|{q.id})),None)
                    if match and match.id not in priority:priority.append(match.id)
            if result.get('source_errors'):errors.extend(result['source_errors'].keys())
            log_queries.append({'query':query.query,'purpose':query.purpose,'year_from':lower,'year_to':end,'sort':sort,'returned':len(result.get('papers',[]))})
        except Exception:
            errors.append('检索超时或未完成')
            log_queries.append({'query':query.query,'purpose':query.purpose,'year_from':lower,'year_to':end,'sort':sort,'returned':0})
    for index,query in enumerate(queries):
        await progress('检索代表性论文 · '+str(len(log_queries)+1))
        await retrieve(query,temporal_anchor=index==0)
    if pool:
        await progress('检查历史覆盖并补充关键论文')
        gaps=await structured(prompts.GAPS,{'field':field['canonical_name'],'papers':material(pool[:20])},Plan)
        for query in gaps.queries[:2]:await retrieve(query)
    if len(pool)<3:
        raise llm.AnalysisError('目前只找到不足3篇带摘要和年份的论文，无法可靠生成领域演进；可稍后重新分析。')
    # Balance old/new evidence within the bounded model context; no vector corpus required.
    early=sorted(pool,key=lambda p:(p.year or end,-(p.citation_count or 0)))[:6]
    recent=sorted(pool,key=lambda p:-(p.year or 0))[:6]
    remainder=sorted(pool,key=lambda p:-(p.citation_count or 0))
    balanced=[]
    for p in [q for pid in priority[:12] for q in pool if q.id==pid]+early+recent+remainder:
        if p.id not in {q.id for q in balanced}:balanced.append(p)
    balanced=balanced[:24]
    await progress('整理时间线、近期热点与潜在方向')
    draft=await structured(prompts.REPORT,{'field':field['canonical_name'],'as_of':end,'recent_year_from':end-2,'papers':material(balanced)},Draft)
    rows=ground(draft,balanced,end)
    if not rows:raise llm.AnalysisError('生成结果没有通过原文证据检查，未保存无依据报告。')
    await progress('逐项核对论文依据')
    review=await structured(prompts.REVIEW,{'field':field['canonical_name'],'papers':material(balanced),
        'entries':[{'index':i,**r} for i,r in enumerate(rows)]},Review)
    allowed=set(review.accepted)
    if any(i<0 or i>=len(rows) for i in allowed):raise llm.AnalysisError('证据核对返回了错误编号')
    accepted=[r for i,r in enumerate(rows) if i in allowed]
    milestones=sorted([r for r in accepted if r['section']=='milestones'],key=lambda r:(r['year'],r['paper_id']))
    if not milestones:raise llm.AnalysisError('没有时间点通过证据核对，原报告仍保留。')
    used={e['paper_id'] for r in accepted for e in r['evidence']}
    oldest=min(p.year for p in balanced)
    # Describe observable retrieval limits; do not publish unchecked model claims
    # about field coverage, publication status or causal inheritance as caveats.
    limitations=['依据公开 API 摘要片段进行归纳，未对入选论文逐篇阅读全文；模型核对仍可能有误，请结合原文判断。']
    limitations.append('基于本次API检索样本归纳；时间线不表示直接引用、技术继承或完整领域历史。')
    if errors:limitations.append('部分检索未完整返回；已保存查到且有摘要依据的结果。')
    if not any(p.year>=end-2 for p in balanced):limitations.append('本次没有近三年样本，不输出近期热点或未来方向。')
    rejected=len(rows)-len(accepted)
    if rejected:limitations.append(f'{rejected}项候选论断未通过证据核对，已省略。')
    return {'field_id':field['id'],'field_name':field['name'],'summary':f'本次基于公开 API 文献样本，核对了 {len(milestones)} 个代表性论文时间点。按发表年份展示演进脉络，近期热点为样本观察，潜在方向为研究假设。',
        'milestones':milestones,'hotspots':[r for r in accepted if r['section']=='hotspots'],
        'directions':[r for r in accepted if r['section']=='directions'],
        'papers':[p.model_dump(mode='json') for p in balanced if p.id in used],
        'coverage':{'candidate_count':len(pool),'analyzed_count':len(balanced),'year_from':oldest,'year_to':end,'recent_year_from':end-2,'queries':log_queries},
        'limitations':limitations,'partial':bool(errors or rejected or len(milestones)<3),
        'prompt_version':prompts.VERSION,'model':llm.model_name(),'generated_at':store.now()}

async def run_report():
    job=await asyncio.to_thread(store.claim_report)
    if not job:return False
    try:
        field=await asyncio.to_thread(store.get_field,job['field_id'])
        result=await asyncio.wait_for(discover(field,job),timeout=1200)
        await asyncio.to_thread(store.finish,job,result)
    except asyncio.CancelledError:
        await asyncio.to_thread(store.finish,job,cancelled=True)
        raise
    except Exception as exc:
        log.exception('Field evolution failed')
        message=exc.message if isinstance(exc,llm.AnalysisError) else '本次领域分析未完成，原有报告已保留，可重新分析。'
        await asyncio.to_thread(store.finish,job,error=message)
    return True

async def report_worker():
    while True:
        try:
            await run_report()
        except asyncio.CancelledError:raise
        except Exception:log.exception('Field report worker failed')
        await asyncio.sleep(2)
