"""index_paper：把论文文本切块、向量化并写入向量库。

按固定字符窗口切分，先不优化切分算法。
文本块和元数据写入 SQLite，向量写入 FAISS。
向量只调用硅基流动 Embedding API。
"""

import asyncio
import logging
import os
import sqlite3
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.services.analysis import fulltext
from app.services.papers import library

log = logging.getLogger(__name__)

CHUNK_CHARS = 800
OVERLAP = 100
MAX_TEXT = 1_000_000


class IndexStoreError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class IndexPaperRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=200)
    text: str | None = Field(None, max_length=MAX_TEXT)
    title: str | None = Field(None, max_length=500)
    workspace_id: str | None = Field(None, max_length=80)


TOOL = {
    "name": "index_paper",
    "description": "将论文文本切成文本块，调用硅基流动 Embedding 转为向量，写入向量库。",
    "parameters": {
        "paper_id": "论文 ID",
        "text": "待索引正文。不提供时使用文献库中的全文或摘要",
        "title": "论文标题，写入元数据",
        "workspace_id": "所属工作区。提供且未附正文时，只索引文献库中的论文",
    },
}


def split_text(text: str) -> list[tuple[int, str]]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [(0, text)]
    step = CHUNK_CHARS - OVERLAP
    parts, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        parts.append((start, text[start:end]))
        if end >= len(text):
            break
        start += step
    return parts


def faiss_path() -> Path:
    from app.infrastructure.demo import visitor, database_path
    if visitor.get():
        return database_path().with_name("vectors.faiss")
    explicit = os.getenv("PAPER_FAISS_PATH", "").strip()
    if explicit:
        return Path(explicit)
    db = Path(os.getenv("PAPER_DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "papers.sqlite3")))
    return db.with_name("vectors.faiss")


def _libs():
    try:
        import faiss
        import numpy as np
    except ImportError as exc:
        raise IndexStoreError("未安装 FAISS，无法保存向量索引") from exc
    return faiss, np


def _ensure(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS vector_chunks (
            chunk_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            workspace_id TEXT,
            faiss_id INTEGER NOT NULL UNIQUE,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            embed_model TEXT NOT NULL,
            vector_dim INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vector_chunks_paper ON vector_chunks(paper_id);
        CREATE TABLE IF NOT EXISTS vector_papers (
            paper_id TEXT PRIMARY KEY,
            workspace_id TEXT,
            title TEXT,
            basis TEXT,
            status TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            embed_model TEXT NOT NULL,
            vector_dim INTEGER NOT NULL,
            message TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vector_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)


def _status(paper_id, workspace_id, title, basis, status, chunk_count, embed_model, vector_dim, message, updated_at=None) -> dict:
    return {
        "tool": "index_paper",
        "paper_id": paper_id,
        "workspace_id": workspace_id,
        "title": title,
        "basis": basis,
        "status": status,
        "chunk_count": chunk_count,
        "embed_model": embed_model,
        "vector_dim": vector_dim,
        "message": message,
        "updated_at": updated_at,
    }


def get_index_status(paper_id: str) -> dict | None:
    with connection() as db:
        _ensure(db)
        row = db.execute("SELECT * FROM vector_papers WHERE paper_id = ?", (paper_id.strip(),)).fetchone()
    if row is None:
        return None
    return _status(
        row["paper_id"], row["workspace_id"], row["title"], row["basis"], row["status"],
        row["chunk_count"], row["embed_model"], row["vector_dim"], row["message"], row["updated_at"],
    )


def _read_index(path: Path):
    faiss, np = _libs()
    if not path.exists():
        return None
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8).copy()
    return faiss.deserialize_index(raw)


def _write_index(path: Path, index) -> None:
    faiss, _np = _libs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(faiss.serialize_index(index).tobytes())
    os.replace(tmp, path)


def _restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    path.write_bytes(snapshot)


def _matrix(np, vectors: list[list[float]]):
    try:
        matrix = np.asarray(vectors, dtype="float32")
    except ValueError as exc:
        raise IndexStoreError("语义向量维度不一致") from exc
    if matrix.ndim != 2 or matrix.shape[0] == 0 or not np.isfinite(matrix).all():
        raise IndexStoreError("语义向量无效")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise IndexStoreError("语义向量无效")
    return np.ascontiguousarray(matrix / norms, dtype=np.float32)


def _reserve(db, count: int) -> list[int]:
    row = db.execute("SELECT value FROM vector_meta WHERE key = 'next_faiss_id'").fetchone()
    start = int(row["value"]) if row else 1
    db.execute(
        "INSERT INTO vector_meta(key, value) VALUES ('next_faiss_id', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(start + count),),
    )
    return list(range(start, start + count))


def _prepare(db, path: Path, paper_id: str, model: str, matrix):
    faiss, np = _libs()
    dim = int(matrix.shape[1])
    other = db.execute(
        "SELECT embed_model, vector_dim FROM vector_chunks WHERE paper_id != ? LIMIT 1",
        (paper_id,),
    ).fetchone()
    if other and (other["embed_model"] != model or int(other["vector_dim"]) != dim):
        raise IndexStoreError("已有索引使用其他向量模型，不能混用")
    old_ids = [int(row["faiss_id"]) for row in db.execute("SELECT faiss_id FROM vector_chunks WHERE paper_id = ?", (paper_id,))]
    if other:
        index = _read_index(path)
        if index is None:
            raise IndexStoreError("向量索引文件缺失，已停止写入")
        if index.d != dim:
            raise IndexStoreError("已有索引维度与当前向量不一致，不能混用")
        if old_ids:
            removed = int(index.remove_ids(np.ascontiguousarray(old_ids, dtype=np.int64)))
            if removed != len(old_ids):
                raise IndexStoreError("向量索引与文本块记录不一致，已停止写入")
    else:
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
    ids = _reserve(db, int(matrix.shape[0]))
    index.add_with_ids(matrix, np.ascontiguousarray(ids, dtype=np.int64))
    return index, ids


def _replace_chunks(db, paper_id, workspace_id, title, basis, model, dim, parts, ids, stamp: str) -> None:
    db.execute("DELETE FROM vector_chunks WHERE paper_id = ?", (paper_id,))
    db.executemany(
        """INSERT INTO vector_chunks (
            chunk_id, paper_id, workspace_id, faiss_id, ordinal, text,
            start_char, end_char, embed_model, vector_dim, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (f"C{ordinal:04d}:{paper_id}", paper_id, workspace_id, faiss_id, ordinal, text,
             start, start + len(text), model, dim, stamp)
            for ordinal, ((start, text), faiss_id) in enumerate(zip(parts, ids), start=1)
        ),
    )
    db.execute(
        """INSERT INTO vector_papers (
            paper_id, workspace_id, title, basis, status, chunk_count, embed_model, vector_dim, message, updated_at
        ) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            workspace_id = excluded.workspace_id,
            title = excluded.title,
            basis = excluded.basis,
            status = excluded.status,
            chunk_count = excluded.chunk_count,
            embed_model = excluded.embed_model,
            vector_dim = excluded.vector_dim,
            message = excluded.message,
            updated_at = excluded.updated_at""",
        (paper_id, workspace_id, title, basis, len(parts), model, dim, "索引完成", stamp),
    )


def _persist(paper_id, workspace_id, title, basis, model, parts, vectors) -> str:
    _faiss, np = _libs()
    matrix = _matrix(np, vectors)
    path = faiss_path()
    written = False
    snapshot = None
    with connection() as db:
        _ensure(db)
        try:
            db.execute("BEGIN IMMEDIATE")
            index, ids = _prepare(db, path, paper_id, model, matrix)
            snapshot = path.read_bytes() if path.exists() else None
            _write_index(path, index)
            written = True
            stamp = datetime.now(timezone.utc).isoformat()
            _replace_chunks(db, paper_id, workspace_id, title, basis, model, int(matrix.shape[1]), parts, ids, stamp)
            db.commit()
            return stamp
        except Exception:
            with suppress(sqlite3.Error):
                db.rollback()
            if written:
                _restore(path, snapshot)
            raise


async def _source(paper_id: str, text: str | None, title: str | None, workspace_id: str | None):
    supplied = (text or "").strip()
    resolved = (title or "").strip() or None
    if supplied:
        return supplied, resolved, "text", None
    paper = await asyncio.to_thread(library.get_paper, paper_id)
    if paper is None:
        if workspace_id:
            return None, resolved, None, "这篇论文不在文献库中。工作区索引只使用已收藏的论文。"
        return None, resolved, None, "没有可索引的文本。请提供正文，或先把论文加入文献库。"
    prepared = await fulltext.prepare(paper)
    body = (prepared.text if prepared else "").strip()
    if not body:
        return None, resolved or paper.title, None, "没有可索引的文本"
    return body, resolved or paper.title, prepared.basis, None


async def index_paper(paper_id: str, text: str | None = None, title: str | None = None, workspace_id: str | None = None) -> dict:
    paper_id = (paper_id or "").strip()
    if not paper_id:
        raise ValueError("请提供论文 ID")
    if len(paper_id) > 200:
        raise ValueError("论文 ID 过长")
    if text is not None and len(text) > MAX_TEXT:
        raise ValueError("文本过长，无法索引")
    workspace_id = (workspace_id or "").strip() or None
    body, resolved_title, basis, error = await _source(paper_id, text, title, workspace_id)
    if error:
        return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, None, None, error)
    if len(body) > MAX_TEXT:
        raise ValueError("文本过长，无法索引")
    parts = split_text(body)
    if not parts:
        return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, None, None, "没有可索引的文本")
    try:
        model, mapping = await embeddings.embed_siliconflow([item for _, item in parts])
    except embeddings.EmbeddingError as exc:
        log.warning("index_paper embedding failed: %s", exc.message)
        return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, None, None, exc.message)
    vectors = []
    dim = None
    for _, chunk in parts:
        vector = mapping.get(chunk)
        if not vector:
            return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, model, None, "语义向量服务返回了无法解析的内容")
        if dim is None:
            dim = len(vector)
        elif len(vector) != dim:
            return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, model, None, "语义向量维度不一致")
        vectors.append(vector)
    try:
        stamp = await asyncio.to_thread(_persist, paper_id, workspace_id, resolved_title, basis, model, parts, vectors)
    except IndexStoreError as exc:
        log.warning("index_paper store failed: %s", exc.message)
        return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, model, dim, exc.message)
    except (OSError, sqlite3.Error):
        log.exception("index_paper store failed")
        return _status(paper_id, workspace_id, resolved_title, basis, "failed", 0, model, dim, "向量库写入失败")
    return _status(paper_id, workspace_id, resolved_title, basis, "completed", len(parts), model, dim, "索引完成", stamp)
