"""将已入库或可通过学术标识解析的论文加入已有工作区。"""

import asyncio
import sqlite3

import httpx
from pydantic import Field

from app.services.papers import library, workspaces
from app.services.retrieval import search


class AddPapersToWorkspaceRequest(workspaces.PaperReferences):
    workspace_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


async def add_papers_to_workspace(workspace_id: str, paper_ids: list[str]) -> dict:
    request = AddPapersToWorkspaceRequest(workspace_id=workspace_id, paper_ids=paper_ids)
    await asyncio.to_thread(workspaces.require, request.workspace_id)
    results = []
    for ref in request.paper_ids:
        try:
            paper = await asyncio.to_thread(library.get_paper, ref)
            if paper is None:
                normalized = library.normalize_reference(ref)
                paper = await asyncio.to_thread(library.get_paper, normalized)
                if paper is None:
                    external = await search.fetch_paper(normalized)
                    if external is None:
                        results.append({"paper_id": ref, "status": "failed", "message": "没有找到这篇论文"})
                        continue
                    paper, _ = await asyncio.to_thread(library.save_paper, external, normalized)
            added = await asyncio.to_thread(workspaces.add, request.workspace_id, paper.local_id)
            results.append({"paper_id": ref, "local_id": paper.local_id,
                            "status": "added" if added else "already_exists",
                            "paper": paper.model_dump(mode="json")})
        except workspaces.WorkspaceNotFound:
            # 目标在批量处理过程中被删除时，后续项同样不能加入。
            results.append({"paper_id": ref, "status": "failed", "message": "指定工作区不存在"})
        except ValueError as exc:
            results.append({"paper_id": ref, "status": "failed", "message": str(exc)})
        except search.SourceFailure as exc:
            results.append({"paper_id": ref, "status": "failed", "message": exc.message})
        except httpx.HTTPError:
            results.append({"paper_id": ref, "status": "failed", "message": "论文来源暂不可用，请稍后重试"})
        except (sqlite3.Error, OSError):
            results.append({"paper_id": ref, "status": "failed", "message": "本地文献库暂不可用，请稍后重试"})
    counts = {status: sum(item["status"] == status for item in results)
              for status in ("added", "already_exists", "failed")}
    return {"tool": "add_papers_to_workspace", "workspace_id": request.workspace_id,
            "status": ("failed" if counts["failed"] == len(results) else "partial") if counts["failed"] else "completed",
            **counts, "results": results}
