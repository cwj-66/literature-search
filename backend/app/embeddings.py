"""Cached title and abstract embeddings from the OpenAI-compatible endpoint."""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import httpx

from .library import connection

log = logging.getLogger(__name__)
BATCH = 10


class EmbeddingError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-v3").strip() or "text-embedding-v3"


def credentials() -> tuple[str, str]:
    silicon = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if silicon:
        base = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
        return silicon, base
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    return key, base


def cache_id(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def load_cached(model: str, texts: list[str]) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    pairs = [(cache_id(model, text), text) for text in texts]
    with connection() as db:
        for start in range(0, len(pairs), 200):
            chunk = pairs[start:start + 200]
            marks = ",".join("?" * len(chunk))
            rows = db.execute(
                f"SELECT cache_key, vector FROM paper_embeddings WHERE model = ? AND cache_key IN ({marks})",
                (model, *(key for key, _ in chunk)),
            ).fetchall()
            by_key = {key: text for key, text in chunk}
            for row in rows:
                text = by_key.get(row["cache_key"])
                if text is None:
                    continue
                try:
                    vector = json.loads(row["vector"])
                except json.JSONDecodeError:
                    continue
                if isinstance(vector, list) and vector and all(isinstance(item, (int, float)) for item in vector):
                    found[text] = [float(item) for item in vector]
    return found


def store_vectors(model: str, vectors: dict[str, list[float]]) -> None:
    if not vectors:
        return
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        db.executemany(
            """INSERT INTO paper_embeddings (cache_key, model, vector, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET model = excluded.model, vector = excluded.vector, created_at = excluded.created_at""",
            ((cache_id(model, text), model, json.dumps(vector), stamp) for text, vector in vectors.items()),
        )


def _vector(row: dict) -> list[float]:
    vector = row.get("embedding") if isinstance(row, dict) else None
    if not isinstance(vector, list) or not vector or not all(isinstance(item, (int, float)) for item in vector):
        raise EmbeddingError("语义向量服务返回了无法解析的内容")
    return [float(item) for item in vector]


async def fetch_vectors(model: str, texts: list[str]) -> dict[str, list[float]]:
    key, base = credentials()
    if not key:
        raise EmbeddingError("未配置向量服务密钥，无法计算语义向量")
    timeout = httpx.Timeout(60.0, connect=10.0)
    found: dict[str, list[float]] = {}
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for start in range(0, len(texts), BATCH):
            batch = texts[start:start + BATCH]
            try:
                response = await client.post(
                    f"{base}/embeddings",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "input": batch, "encoding_format": "float"},
                )
            except httpx.TimeoutException as exc:
                raise EmbeddingError("语义向量请求超时") from exc
            except httpx.HTTPError as exc:
                raise EmbeddingError("语义向量请求失败") from exc
            if response.status_code != 200:
                log.warning("embedding request failed: %s", response.status_code)
                raise EmbeddingError("语义向量请求失败")
            try:
                rows = response.json().get("data")
            except ValueError as exc:
                raise EmbeddingError("语义向量服务返回了无法解析的内容") from exc
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise EmbeddingError("语义向量服务返回了无法解析的内容")
            ordered = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: row.get("index", 0))
            if len(ordered) != len(batch):
                raise EmbeddingError("语义向量服务返回了无法解析的内容")
            for text, row in zip(batch, ordered):
                found[text] = _vector(row)
    return found


async def embed_many(texts: list[str]) -> dict[str, list[float]]:
    unique = []
    for text in texts:
        if text and text not in unique:
            unique.append(text)
    if not unique:
        return {}
    model = model_name()
    cached = await asyncio.to_thread(load_cached, model, unique)
    missing = [text for text in unique if text not in cached]
    if missing:
        fresh = await fetch_vectors(model, missing)
        await asyncio.to_thread(store_vectors, model, fresh)
        cached.update(fresh)
    return {text: cached[text] for text in unique if text in cached}
