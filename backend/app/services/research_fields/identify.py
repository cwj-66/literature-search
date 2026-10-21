"""Lightweight migration for already-analyzed papers; new analyses tag fields inline."""
import asyncio
import json
import logging
from app.infrastructure import llm
from app.infrastructure.db import connection
from app.schemas import AnalysisClaim, ExtractionOutput
from app.services.analysis import evidence_analysis as ea
from app.services.research_fields import prompts, store

log=logging.getLogger(__name__)

async def run_one():
    def claim():
        with connection() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT local_id FROM field_identifications WHERE status='queued' ORDER BY updated_at LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE field_identifications SET status='running',updated_at=? WHERE local_id=?",(store.now(),row[0]))
            return row[0]
    local_id=await asyncio.to_thread(claim)
    if not local_id:return False
    try:
        def load():
            with connection() as db:
                paper=db.execute('SELECT data FROM papers WHERE local_id=?',(local_id,)).fetchone()
                result=db.execute('SELECT payload FROM analysis_results WHERE local_id=? ORDER BY generated_at DESC LIMIT 1',(local_id,)).fetchone()
                return json.loads(paper[0]) if paper else None,json.loads(result[0]) if result else {}
        paper,old=await asyncio.to_thread(load)
        if not paper:raise ValueError('论文已被移除')
        from app.schemas import ResearchField
        if old.get('research_fields'):
            fields=[ResearchField.model_validate(f) for f in old['research_fields']][:3]
        else:
            text='标题：'+paper['title']+'\n\n摘要：'+(paper.get('abstract') or '')
            # Reuse already-grounded paper text if an upload has no abstract.
            if not paper.get('abstract'):
                text+='\n\n'+'\n\n'.join(p['text'] for p in old.get('source_paragraphs',[])[:8])[:10000]
            source=ea.paragraphs(text)
            messages=[{'role':'system','content':'只识别研究领域，不输出其他分析。'+prompts.FIELD_RULES},
                      {'role':'user','content':json.dumps([p.model_dump() for p in source],ensure_ascii=False)}]
            output=await asyncio.wait_for(ea.extract(messages),timeout=90)
            claims=ea.grounded(output.claims,source,True)
            checked=await asyncio.wait_for(ea.review([c for c in claims if c.kind=='research_field' and c.research_field],source),timeout=90)
            fields=list({c.research_field.canonical_name.casefold():c.research_field for c in checked}.values())[:3]
        def save():
            with connection() as db,db:
                if db.execute("SELECT status FROM field_identifications WHERE local_id=?", (local_id,)).fetchone()[0] == 'running':
                    store.save_assignments(db,local_id,fields)
        await asyncio.to_thread(save)
    except asyncio.CancelledError:
        def requeue():
            with connection() as db,db:db.execute("UPDATE field_identifications SET status='queued',updated_at=? WHERE local_id=? AND status='running'",(store.now(),local_id))
        await asyncio.to_thread(requeue)
        raise
    except Exception:
        log.exception('Legacy field identification failed')
        def fail():
            with connection() as db,db:db.execute("UPDATE field_identifications SET status='failed',error='领域识别未完成，可重试',updated_at=? WHERE local_id=?",(store.now(),local_id))
        await asyncio.to_thread(fail)
    return True

def recover():
    with connection() as db,db:
        db.execute("UPDATE field_identifications SET status='queued' WHERE status='running'")

async def worker():
    await asyncio.to_thread(recover)
    while True:
        try:await asyncio.wait_for(run_one(),timeout=300)
        except asyncio.CancelledError:raise
        except Exception:log.exception('Field identification worker failed')
        await asyncio.sleep(2)
