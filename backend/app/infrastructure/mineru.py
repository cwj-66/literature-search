"""Parse a local PDF through the MinerU cloud API."""

import asyncio
import io
import logging
import os
import zipfile
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger(__name__)

HOSTS = ("mineru.net", "openxlab.org.cn", "aliyuncs.com")


class MinerUError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _token() -> str:
    token = os.getenv("MINERU_API_TOKEN", "").strip()
    if not token:
        raise MinerUError("未配置 MinerU API Token，请在 MINERU_API_TOKEN 中填写")
    return token


def _base() -> str:
    return os.getenv("MINERU_API_BASE", "https://mineru.net").rstrip("/")


def _allowed(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not any(host == name or host.endswith("." + name) for name in HOSTS):
        raise MinerUError("MinerU 返回了无法使用的文件地址")
    return url


def _data(payload: object, action: str) -> dict:
    if not isinstance(payload, dict):
        raise MinerUError(f"{action}返回了无法解析的内容")
    if payload.get("code", 0) not in (0, "0", None):
        raise MinerUError(str(payload.get("msg") or f"{action}失败"))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MinerUError(f"{action}返回了无法解析的内容")
    return data


def markdown_from_zip(blob: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise MinerUError("MinerU 解析结果不是有效的压缩包") from exc
    with archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".md") and not name.endswith("/")]
        names.sort(key=lambda name: (0 if name.lower().endswith("full.md") else 1, len(name)))
        if not names:
            raise MinerUError("MinerU 解析结果里没有 Markdown 正文")
        text = archive.read(names[0]).decode("utf-8", errors="replace").strip()
    if not text:
        raise MinerUError("MinerU 解析结果里没有 Markdown 正文")
    return text


async def parse_pdf(data: bytes, filename: str = "paper.pdf") -> str:
    polls = int(os.getenv("MINERU_POLL_LIMIT", "120"))
    interval = float(os.getenv("MINERU_POLL_INTERVAL", "2"))
    timeout = httpx.Timeout(120.0, connect=15.0)
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                f"{_base()}/api/v4/file-urls/batch",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "files": [{"name": filename, "data_id": "paper"}],
                    "model_version": os.getenv("MINERU_MODEL", "vlm").strip() or "vlm",
                    "enable_formula": True,
                    "enable_table": True,
                    "language": "ch",
                },
            )
            if response.status_code != 200:
                raise MinerUError("申请 MinerU 上传链接失败")
            payload = _data(response.json(), "申请上传链接")
            batch_id = str(payload.get("batch_id") or "")
            urls = payload.get("file_urls") or []
            if not batch_id or not isinstance(urls, list) or not urls:
                raise MinerUError("MinerU 没有返回上传链接")
            uploaded = await client.put(_allowed(str(urls[0])), content=data)
            if uploaded.status_code not in (200, 201):
                raise MinerUError("PDF 上传到 MinerU 失败")
            result_url = f"{_base()}/api/v4/extract-results/batch/{quote(batch_id, safe='')}"
            for _ in range(polls):
                await asyncio.sleep(interval)
                polled = await client.get(result_url, headers=headers)
                if polled.status_code != 200:
                    continue
                body = _data(polled.json(), "查询解析结果")
                rows = body.get("extract_result") or []
                if isinstance(rows, dict):
                    rows = [rows]
                if not rows:
                    continue
                selected = next((row for row in rows if row.get("file_name") == filename), rows[0])
                state = str(selected.get("state") or "").lower()
                if state == "done":
                    zip_url = selected.get("full_zip_url")
                    if not zip_url:
                        raise MinerUError("MinerU 解析完成但没有结果文件")
                    downloaded = await client.get(_allowed(str(zip_url)))
                    if downloaded.status_code != 200:
                        raise MinerUError("下载 MinerU 解析结果失败")
                    return markdown_from_zip(downloaded.content)
                if state == "failed":
                    raise MinerUError(str(selected.get("err_msg") or "MinerU 解析失败"))
    except MinerUError:
        raise
    except httpx.HTTPError as exc:
        log.warning("mineru request failed: %s", exc)
        raise MinerUError("无法连接 MinerU") from exc
    raise MinerUError("MinerU 解析超时，请稍后重试")
