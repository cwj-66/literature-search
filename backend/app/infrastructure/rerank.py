"""SiliconFlow reranker; fused ranks are kept when the service is unavailable."""

import logging
import os
from app.infrastructure.config import value as provider_value

import httpx

log = logging.getLogger(__name__)


class RerankError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def model_name() -> str:
    return provider_value("RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip() or "BAAI/bge-reranker-v2-m3"


async def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    if not documents:
        return []
    key = provider_value("SILICONFLOW_API_KEY", "").strip()
    if not key:
        raise RerankError("未配置硅基流动密钥，无法重排序")
    base = provider_value("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    payload = {
        "model": model_name(),
        "query": query,
        "documents": documents,
        "return_documents": False,
        "top_n": min(top_n or len(documents), len(documents)),
    }
    timeout = httpx.Timeout(45.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{base}/rerank",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise RerankError("重排序请求超时") from exc
    except httpx.HTTPError as exc:
        raise RerankError("重排序请求失败") from exc
    if response.status_code != 200:
        log.warning("rerank failed: %s", response.status_code)
        raise RerankError("重排序请求失败")
    try:
        rows = response.json().get("results")
    except ValueError as exc:
        raise RerankError("重排序结果无法解析") from exc
    if not isinstance(rows, list):
        raise RerankError("重排序结果无法解析")
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        score = row.get("relevance_score")
        if isinstance(index, int) and 0 <= index < len(documents) and isinstance(score, (int, float)):
            ranked.append((index, float(score)))
    if not ranked:
        raise RerankError("重排序结果无法解析")
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked
