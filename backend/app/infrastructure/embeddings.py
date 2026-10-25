"""Cached title and abstract embeddings from the OpenAI-compatible endpoint."""

import asyncio
import hashlib
import json
import logging
import os
from app.infrastructure.config import value as provider_value
from datetime import datetime, timezone

import httpx

from app.infrastructure.db import connection

log = logging.getLogger(__name__)
BATCH = 10


class EmbeddingError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def model_name() -> str:
    return siliconflow_model()


def credentials() -> tuple[str, str]:
    key = provider_value("SILICONFLOW_API_KEY", "").strip()
    if not key:
        raise EmbeddingError("未配置硅基流动密钥，无法计算语义向量")
    base = provider_value("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
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


async def fetch_vectors(model: str, texts: list[str], key: str | None = None, base: str | None = None) -> dict[str, list[float]]:
    if not key or not base:
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
                try:
                    error = response.json()
                    detail = error.get("error", error)
                    message = str(detail.get("message", "")) if isinstance(detail, dict) else ""
                except (ValueError, AttributeError):
                    message = ""
                if "free quota exhausted" in message.casefold():
                    raise EmbeddingError("向量模型免费额度已用尽，账号处于仅使用免费额度模式；请在服务商控制台处理额度或更换可用向量服务")
                if response.status_code in (401, 403):
                    raise EmbeddingError("向量服务认证或访问权限不足，请检查服务配置")
                if response.status_code == 429:
                    raise EmbeddingError("向量服务限流，请稍后重试")
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


async def embed_many(
    texts: list[str], model: str | None = None, key: str | None = None, base: str | None = None, persist: bool = True,
) -> dict[str, list[float]]:
    unique = []
    for text in texts:
        if text and text not in unique:
            unique.append(text)
    if not unique:
        return {}
    model = model or model_name()
    cached = await asyncio.to_thread(load_cached, model, unique) if persist else {}
    missing = [text for text in unique if text not in cached]
    if missing:
        fresh = await fetch_vectors(model, missing, key, base)
        if persist:
            await asyncio.to_thread(store_vectors, model, fresh)
        cached.update(fresh)
    return {text: cached[text] for text in unique if text in cached}


def siliconflow_model() -> str:
    return provider_value("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3"


async def embed_siliconflow(texts: list[str], *, persist: bool = True) -> tuple[str, dict[str, list[float]]]:
    key = provider_value("SILICONFLOW_API_KEY", "").strip()
    if not key:
        raise EmbeddingError("未配置硅基流动密钥，无法计算语义向量")
    base = provider_value("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = siliconflow_model()
    return model, await embed_many(texts, model, key, base, persist=persist)
