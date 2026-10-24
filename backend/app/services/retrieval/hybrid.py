"""Project-scoped keyword plus vector retrieval fused with RRF."""

import json
import math
import re
import sqlite3
from datetime import datetime, timezone

from app.infrastructure import embeddings, rerank
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.analysis.evidence_analysis import paragraphs

SPLIT_VERSION = "c2400-o400-v1"
STRUCTURED_SPLIT_VERSION = "sections-c2400-o400-v2"
CHUNK_CHARS = 2400
OVERLAP = 400
RRF_K = 60


def split_text(text: str) -> list[tuple[int, str]]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [(0, text)]
    parts, start = [], 0
    while start < len(text):
        parts.append((start, text[start:start + CHUNK_CHARS]))
        if start + CHUNK_CHARS >= len(text):
            break
        start += CHUNK_CHARS - OVERLAP
    return parts


def ensure(db) -> None:
    statements = """
        CREATE TABLE IF NOT EXISTS paper_chunks (
            chunk_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            local_id TEXT,
            section TEXT,
            text TEXT NOT NULL,
            paragraph_ids TEXT NOT NULL,
            start_char INTEGER NOT NULL,
            embed_model TEXT,
            split_version TEXT NOT NULL,
            vector TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_paper ON paper_chunks(paper_id);
        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """
    for statement in statements.split(";"):
        if statement.strip():
            db.execute(statement)
    columns = {row[1] for row in db.execute("PRAGMA table_info(paper_chunks)")}
    for name, declaration in {
        "basis": "TEXT NOT NULL DEFAULT 'unknown'", "chunk_index": "INTEGER",
        "page_start": "INTEGER", "page_end": "INTEGER", "source_hash": "TEXT",
        "parent_id": "TEXT", "section_path": "TEXT", "source_start": "INTEGER",
        "source_end": "INTEGER", "token_count": "INTEGER",
    }.items():
        if name not in columns:
            try:
                db.execute(f"ALTER TABLE paper_chunks ADD COLUMN {name} {declaration}")
            except sqlite3.OperationalError:
                if name not in {r[1] for r in db.execute("PRAGMA table_info(paper_chunks)")}:
                    raise
    db.execute('''CREATE TABLE IF NOT EXISTS qa_parent_chunks (
        parent_id TEXT PRIMARY KEY,paper_id TEXT NOT NULL,local_id TEXT,source_hash TEXT NOT NULL,
        text TEXT NOT NULL,section TEXT,section_path TEXT NOT NULL,paragraph_ids TEXT NOT NULL,
        page_start INTEGER,page_end INTEGER,source_start INTEGER,source_end INTEGER,token_count INTEGER)''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_qa_parent_paper ON qa_parent_chunks(paper_id,source_hash)')
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fts'").fetchone()
    if not row:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(chunk_id UNINDEXED, paper_id UNINDEXED, body, tokenize='unicode61')")


def fts_query(text: str) -> str:
    terms = re.findall(r"[\w\u4e00-\u9fff]+", text)[:16]
    if not terms:
        return '""'
    return " OR ".join('"' + term.replace('"', "") + '"' for term in terms if term)


def rrf(rank_lists: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, doc_id in enumerate(ranks, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)


def cap_per_paper(chunk_ids: list[str], by_id: dict, limit: int = 3) -> list[str]:
    used: dict[str, int] = {}
    kept = []
    for chunk_id in chunk_ids:
        paper_id = by_id[chunk_id]["paper_id"]
        if used.get(paper_id, 0) >= limit:
            continue
        used[paper_id] = used.get(paper_id, 0) + 1
        kept.append(chunk_id)
    return kept


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    n1 = math.sqrt(sum(a * a for a in left))
    n2 = math.sqrt(sum(b * b for b in right))
    if not n1 or not n2:
        return 0.0
    return dot / (n1 * n2)


def index_prepared(paper: PaperOut, prepared, local_id: str | None = None) -> list[dict]:
    existing = load_chunks([paper.id])
    if any(row.get("basis") == "full_text" and row.get("source_hash") for row in existing.values()):
        return list(existing.values())  # Durable QA jobs own full-text replacements.
    source = paragraphs(prepared.text)
    rows = []
    for index, (offset, text) in enumerate(split_text(prepared.text), start=1):
        chunk_id = f"C{index:04d}:{paper.id}"
        covered = [p.id for p in source if compact_overlap(p.text, text)]
        section = next((p.section for p in source if p.id in covered), "正文")
        rows.append({
            "chunk_id": chunk_id,
            "paper_id": paper.id,
            "local_id": local_id,
            "section": section,
            "text": text,
            "paragraph_ids": covered,
            "start_char": offset,
            "split_version": SPLIT_VERSION,
            "basis": prepared.basis,
        })
    model = embeddings.model_name()
    with connection() as db, db:
        ensure(db)
        if db.execute("SELECT 1 FROM paper_chunks WHERE paper_id=? AND basis='full_text' AND source_hash IS NOT NULL LIMIT 1", (paper.id,)).fetchone():
            return list(existing.values())
        db.execute("DELETE FROM paper_fts WHERE paper_id = ?", (paper.id,))
        db.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper.id,))
        for chunk_index, row in enumerate(rows, 1):
            db.execute(
                """INSERT INTO paper_chunks
                (chunk_id,paper_id,local_id,section,text,paragraph_ids,start_char,embed_model,split_version,vector,basis,chunk_index)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)""",
                (row["chunk_id"], row["paper_id"], row["local_id"], row["section"], row["text"],
                 json.dumps(row["paragraph_ids"]), row["start_char"], SPLIT_VERSION, prepared.basis, chunk_index),
            )
            db.execute("INSERT INTO paper_fts(chunk_id, paper_id, body) VALUES (?, ?, ?)",
                       (row["chunk_id"], row["paper_id"], row["text"]))
        db.execute(
            "INSERT INTO index_meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("split_version", SPLIT_VERSION),
        )
        db.execute(
            "INSERT INTO index_meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("embed_model", model),
        )
        db.execute(
            "INSERT INTO index_meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("updated_at", datetime.now(timezone.utc).isoformat()),
        )
    return rows


def compact_overlap(left: str, right: str) -> bool:
    a = re.sub(r"\s+", "", left).casefold()
    b = re.sub(r"\s+", "", right).casefold()
    return bool(a) and a[:80] in b


def load_chunks(paper_ids: list[str] | None = None) -> dict[str, dict]:
    from app.services.retrieval.qa_chunking import VERSION
    if paper_ids == []:
        return {}
    with connection() as db:
        ensure(db)
        if paper_ids:
            marks = ",".join("?" * len(paper_ids))
            rows = db.execute(
                f"SELECT * FROM paper_chunks WHERE paper_id IN ({marks}) AND split_version IN (?,?,?)",
                (*paper_ids, SPLIT_VERSION, STRUCTURED_SPLIT_VERSION, VERSION),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM paper_chunks WHERE split_version IN (?,?,?)", (SPLIT_VERSION, STRUCTURED_SPLIT_VERSION, VERSION)).fetchall()
        found = {}
        for row in rows:
            found[row["chunk_id"]] = {
                "chunk_id": row["chunk_id"],
                "paper_id": row["paper_id"],
                "local_id": row["local_id"],
                "section": row["section"],
                "text": row["text"],
                "paragraph_ids": json.loads(row["paragraph_ids"]),
                "start_char": row["start_char"],
                "embed_model": row["embed_model"],
                "basis": row["basis"],
                "chunk_index": row["chunk_index"],
                "page_start": row["page_start"], "page_end": row["page_end"],
                "source_hash": row["source_hash"],
                "parent_id": row['parent_id'],
                "section_path": json.loads(row['section_path']) if row['section_path'] else [],
                "source_start":row['source_start'],"source_end":row['source_end'],"token_count":row['token_count'],
                "vector": json.loads(row["vector"]) if row["vector"] else None,
            }
        return found


def parent_chunks(hits: list[dict], paper_ids: list[str]) -> dict[str, dict]:
    """Expand only a hit's own paper/revision; never load unrelated vectors."""
    keys=list(dict.fromkeys((h['paper_id'],h.get('source_hash'),h.get('parent_id'))
                           for h in hits if h['paper_id'] in paper_ids and h.get('parent_id')))
    if not keys:return {}
    with connection() as db:
        ensure(db)
        result={}
        for pid,rev,parent in keys:
            row=db.execute('SELECT * FROM qa_parent_chunks WHERE parent_id=? AND paper_id=? AND source_hash=?',
                           (parent,pid,rev)).fetchone()
            if row:
                r=dict(row);r['paragraph_ids']=json.loads(r['paragraph_ids']);r['section_path']=json.loads(r['section_path'])
                result[parent]=r
        return result


def keyword_search(query: str, paper_ids: list[str] | None, limit: int, *, body_only: bool = False) -> list[str]:
    if paper_ids == []:
        return []
    match = fts_query(query)
    content_filter = (" AND chunk_id IN (SELECT chunk_id FROM paper_chunks WHERE "
        "lower(trim(coalesce(section,''))) NOT IN ('references','bibliography','参考文献') "
        "AND lower(trim(coalesce(section,''))) NOT LIKE '% references' "
        "AND lower(trim(coalesce(section,''))) NOT LIKE '% bibliography')") if body_only else ''
    with connection() as db:
        ensure(db)
        try:
            if paper_ids:
                marks = ",".join("?" * len(paper_ids))
                rows = db.execute(
                    f"""SELECT chunk_id FROM paper_fts WHERE paper_fts MATCH ? AND paper_id IN ({marks}) {content_filter} ORDER BY bm25(paper_fts) LIMIT ?""",
                    (match, *paper_ids, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT chunk_id FROM paper_fts WHERE paper_fts MATCH ? {content_filter} ORDER BY bm25(paper_fts) LIMIT ?",
                    (match, limit),
                ).fetchall()
        except Exception:
            return []
        return [row[0] for row in rows]


def adjacent_chunks(hits: list[dict], paper_ids: list[str]) -> list[dict]:
    """Read bounded neighbouring text only, not every selected document's vectors."""
    keys=list(dict.fromkeys((h["paper_id"],h["source_hash"],h["chunk_index"])
              for h in hits if h.get("paper_id") in paper_ids and h.get("source_hash") and h.get("chunk_index")))
    if not keys or not paper_ids:
        return []
    marks=",".join("?" for _ in paper_ids)
    clauses=" OR ".join("(paper_id=? AND source_hash=? AND chunk_index IN (?,?))" for _ in keys)
    params=[*paper_ids]
    for pid,rev,index in keys:
        params.extend([pid,rev,index-1,index+1])
    with connection() as db:
        ensure(db)
        rows=db.execute(f"""SELECT chunk_id,paper_id,local_id,section,text,paragraph_ids,start_char,
            embed_model,basis,chunk_index,page_start,page_end,source_hash,parent_id,section_path,
            source_start,source_end,token_count FROM paper_chunks
            WHERE paper_id IN ({marks}) AND ({clauses})""",params).fetchall()
        return [{**dict(row),"paragraph_ids":json.loads(row["paragraph_ids"]),
                 'section_path':json.loads(row['section_path']) if row['section_path'] else []} for row in rows]


async def store_vectors(chunks: dict[str, dict]) -> None:
    model = embeddings.model_name()
    missing = [item for item in chunks.values() if not item.get("vector") or item.get("embed_model") != model]
    if not missing:
        return
    vectors = await embeddings.embed_many([item["text"] for item in missing])
    with connection() as db, db:
        for item in missing:
            vector = vectors.get(item["text"])
            if not vector:
                continue
            item["vector"] = vector
            item["embed_model"] = model
            db.execute(
                "UPDATE paper_chunks SET vector = ?, embed_model = ? WHERE chunk_id = ? AND text = ?",
                (json.dumps(vector), model, item["chunk_id"], item["text"]),
            )


def rerank_documents(query: str, hits: list[dict]) -> list[str]:
    """New QA children keep complete objects through reranking too."""
    documents=[h['text'] if h.get('parent_id') else h['text'][:4000] for h in hits]
    if rerank.model_name() == 'BAAI/bge-reranker-v2-m3' and any(h.get('parent_id') for h in hits):
        from app.services.retrieval.qa_chunking import count
        reserve=count(query)+4
        if any(h.get('parent_id') and count(d)+reserve>8192 for h,d in zip(hits,documents)):
            raise rerank.RerankError('完整QA表格超过重排输入上限，保留RRF顺序')
    return documents


async def vector_search(query: str, chunks: dict[str, dict], limit: int) -> list[str]:
    try:
        await store_vectors(chunks)
        mapping = await embeddings.embed_many([query])
    except embeddings.EmbeddingError:
        return []
    query_vec = mapping.get(query)
    if not query_vec:
        return []
    model = embeddings.model_name()
    scored = []
    for chunk_id, item in chunks.items():
        vector = item.get("vector")
        if not vector or item.get("embed_model") not in (None, model):
            continue
        scored.append((cosine(query_vec, vector), chunk_id))
    scored.sort(reverse=True)
    for score, chunk_id in scored[:limit]:
        chunks[chunk_id]["vector_score"] = score
    return [chunk_id for _, chunk_id in scored[:limit]]


def index_abstracts(papers: list[PaperOut]) -> None:
    from app.services.analysis.fulltext import PreparedText
    for paper in papers:
        existing = load_chunks([paper.id])
        if existing:
            continue
        text = paper.abstract or paper.title
        prepared = PreparedText(text, "abstract", "abstract", None, None, {"truncated": False})
        index_prepared(paper, prepared, getattr(paper, "local_id", None))


async def retrieve(query: str, paper_ids: list[str] | None = None, k_kw: int = 20, k_vec: int = 20, k_rerank: int = 30, k_final: int = 10) -> list[dict]:
    chunks = load_chunks(paper_ids)
    reference_question=bool(re.search(r'参考文献|引用(?:了|的|哪些|次数)|bibliograph|\breferences\b|cited papers',query,re.I))
    if not reference_question:
        chunks={cid:c for cid,c in chunks.items() if not re.search(r'^(?:\d+[.\s]*)?(?:references|bibliography|参考文献)\s*$',c.get('section') or '',re.I)}
    if not chunks:
        return []
    keyword_ids = keyword_search(query, paper_ids, k_kw, body_only=not reference_question)
    vector_ids = await vector_search(query, chunks, k_vec)
    fused = cap_per_paper([cid for cid in rrf([keyword_ids, vector_ids]) if cid in chunks], chunks,
                          limit=k_final if paper_ids and len(paper_ids) == 1 else 3)
    fused = fused[:k_rerank] or list(chunks)[:k_rerank]
    order = fused
    scores = {}
    try:
        documents = rerank_documents(query,[chunks[cid] for cid in fused])
        ranked = await rerank.rerank(query, documents, top_n=min(k_final, len(documents)))
        order = [fused[index] for index, _ in ranked if type(index) is int and 0 <= index < len(fused)]
        scores = {fused[index]: score for index, score in ranked if type(index) is int and 0 <= index < len(fused)}
    except rerank.RerankError:
        order = fused
    result = []
    for chunk_id in order[:k_final]:
        item = dict(chunks[chunk_id])
        item.pop("vector", None)
        item["keyword_match"] = chunk_id in keyword_ids
        item["rerank_score"] = scores.get(chunk_id)
        result.append(item)
    return result
