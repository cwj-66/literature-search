"""Project-scoped keyword plus vector retrieval fused with RRF."""

import json
import math
import re
from datetime import datetime, timezone

from app.infrastructure import embeddings, rerank
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.analysis.evidence_analysis import paragraphs

SPLIT_VERSION = "c2400-o400-v1"
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
    db.executescript("""
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
    """)
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fts'").fetchone()
    if not row:
        db.execute("CREATE VIRTUAL TABLE paper_fts USING fts5(chunk_id UNINDEXED, paper_id UNINDEXED, body, tokenize='unicode61')")


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
        })
    model = embeddings.model_name()
    with connection() as db, db:
        ensure(db)
        db.execute("DELETE FROM paper_fts WHERE paper_id = ?", (paper.id,))
        db.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper.id,))
        for row in rows:
            db.execute(
                """INSERT INTO paper_chunks VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)""",
                (row["chunk_id"], row["paper_id"], row["local_id"], row["section"], row["text"],
                 json.dumps(row["paragraph_ids"]), row["start_char"], SPLIT_VERSION),
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
    with connection() as db:
        ensure(db)
        if paper_ids:
            marks = ",".join("?" * len(paper_ids))
            rows = db.execute(
                f"SELECT * FROM paper_chunks WHERE paper_id IN ({marks}) AND split_version = ?",
                (*paper_ids, SPLIT_VERSION),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM paper_chunks WHERE split_version = ?", (SPLIT_VERSION,)).fetchall()
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
                "vector": json.loads(row["vector"]) if row["vector"] else None,
            }
        return found


def keyword_search(query: str, paper_ids: list[str] | None, limit: int) -> list[str]:
    match = fts_query(query)
    with connection() as db:
        ensure(db)
        try:
            if paper_ids:
                marks = ",".join("?" * len(paper_ids))
                rows = db.execute(
                    f"""SELECT chunk_id FROM paper_fts WHERE paper_fts MATCH ? AND paper_id IN ({marks}) LIMIT ?""",
                    (match, *paper_ids, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT chunk_id FROM paper_fts WHERE paper_fts MATCH ? LIMIT ?",
                    (match, limit),
                ).fetchall()
        except Exception:
            return []
        return [row[0] for row in rows]


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
                "UPDATE paper_chunks SET vector = ?, embed_model = ? WHERE chunk_id = ?",
                (json.dumps(vector), model, item["chunk_id"]),
            )


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
    return [chunk_id for _, chunk_id in scored[:limit]]


def index_abstracts(papers: list[PaperOut]) -> None:
    from app.services.analysis.fulltext import PreparedText
    for paper in papers:
        if load_chunks([paper.id]):
            continue
        text = paper.abstract or paper.title
        prepared = PreparedText(text, "abstract", "abstract", None, None, {"truncated": False})
        index_prepared(paper, prepared, getattr(paper, "local_id", None))


async def retrieve(query: str, paper_ids: list[str] | None = None, k_kw: int = 20, k_vec: int = 20, k_rerank: int = 30, k_final: int = 10) -> list[dict]:
    chunks = load_chunks(paper_ids)
    if not chunks:
        return []
    keyword_ids = keyword_search(query, paper_ids, k_kw)
    vector_ids = await vector_search(query, chunks, k_vec)
    fused = cap_per_paper(rrf([keyword_ids, vector_ids]), chunks)
    fused = fused[:k_rerank] or list(chunks)[:k_rerank]
    documents = [chunks[chunk_id]["text"][:4000] for chunk_id in fused]
    order = fused
    try:
        ranked = await rerank.rerank(query, documents, top_n=min(k_final, len(documents)))
        order = [fused[index] for index, _ in ranked if index < len(fused)]
    except rerank.RerankError:
        order = fused
    result = []
    for chunk_id in order[:k_final]:
        item = dict(chunks[chunk_id])
        item.pop("vector", None)
        result.append(item)
    return result
