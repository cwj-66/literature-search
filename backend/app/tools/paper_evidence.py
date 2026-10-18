"""Read existing SQLite/FAISS indices; retrieval never parses or reindexes papers."""

import asyncio
import re

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.services.papers import documents
from app.services.retrieval import hybrid
from app.tools import index_paper as index_tool


def chunks_for(papers):
    aliases = list(dict.fromkeys(ref for p in papers for ref in (p.id, p.local_id)))
    if not aliases:
        return []
    with connection() as db:
        index_tool._ensure(db)
        marks = ",".join("?" for _ in aliases)
        rows = [dict(row) for row in db.execute(
            f"""SELECT c.*, p.basis FROM vector_chunks c LEFT JOIN vector_papers p ON p.paper_id=c.paper_id
                WHERE c.paper_id IN ({marks}) ORDER BY c.paper_id, c.ordinal""", aliases)]
    # The older research index remains readable, but is never populated implicitly here.
    by_alias = {ref: p for p in papers for ref in (p.id, p.local_id)}
    indexed = {by_alias[row["paper_id"]].local_id for row in rows}
    older = hybrid.load_chunks(aliases)
    rows.extend({**row, "ordinal": None} for row in older.values() if by_alias[row["paper_id"]].local_id not in indexed)
    structures = {p.local_id: documents.get_structure(p.local_id) for p in papers}
    for row in rows:
        paper = by_alias[row["paper_id"]]
        if not row.get("basis"):
            row["basis"] = "unknown"
        row["local_id"] = paper.local_id
        row["paper_id"] = paper.id
        row.setdefault("end_char", row["start_char"] + len(row["text"]))
        row["page_number"] = None
        row["page_end"] = None
        structure = structures[paper.local_id]
        # Offsets below are valid only when the index was built from this exact structured text.
        if structure:
            source = structure.get("text", "")
            if source[row["start_char"]:row["end_char"]] == row["text"]:
                offset, pages, sections = 0, [], []
                for block in structure.get("blocks", []):
                    body = block.get("text") or ""
                    if not body:
                        continue
                    end = offset + len(body)
                    if offset < row["end_char"] and end > row["start_char"]:
                        if block.get("page_number") is not None:
                            pages.append(block["page_number"])
                        if block.get("section_id"):
                            sections.append(block["section_id"])
                    offset = end + 2
                row["page_number"] = min(pages) if pages else None
                row["page_end"] = max(pages) if pages else None
                titles = {s["id"]: s["title"] for s in structure.get("sections", [])}
                row["section"] = titles.get(next(iter(sections), None))
    return rows


def _semantic_scores(rows, vector, model):
    index = index_tool._read_index(index_tool.faiss_path())
    if index is None:
        raise index_tool.IndexStoreError("向量索引文件缺失")
    _faiss, np = index_tool._libs()
    query = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(query))
    if index.d != len(query) or not norm or not np.isfinite(query).all():
        raise index_tool.IndexStoreError("查询向量维度或数值无效")
    query /= norm
    return {row["chunk_id"]: float(np.dot(index.reconstruct(int(row["faiss_id"])), query))
            for row in rows if "faiss_id" in row and row["embed_model"] == model}


async def retrieve(papers, query: str, limit: int):
    rows = await asyncio.to_thread(chunks_for, papers)
    covered = {row["local_id"] for row in rows}
    unindexed = [p.local_id for p in papers if p.local_id not in covered]
    if not rows:
        return {"status": "not_indexed", "evidence": [], "unindexed_paper_ids": unindexed,
                "warnings": ["当前范围没有已保存的文本块，请先完成后台论文导入。"]}
    warnings, semantic = [], {}
    if any("faiss_id" in row for row in rows):
        try:
            model, mapping = await embeddings.embed_siliconflow([query])
            semantic = await asyncio.to_thread(_semantic_scores, rows, mapping[query], model)
            if not semantic:
                warnings.append("查询模型与已有索引不一致，仅使用关键词检索。")
        except (embeddings.EmbeddingError, index_tool.IndexStoreError, OSError, RuntimeError, KeyError) as exc:
            warnings.append("语义检索暂不可用，仅使用关键词检索。")
    legacy = [row for row in rows if row.get("vector") and row.get("embed_model") == embeddings.model_name()]
    if legacy:
        try:
            mapping = await embeddings.embed_many([query])
            semantic.update({row["chunk_id"]: hybrid.cosine(mapping[query], row["vector"]) for row in legacy})
        except (embeddings.EmbeddingError, KeyError):
            warnings.append("旧版向量检索暂不可用，仅使用关键词检索。")
    terms = re.findall(r"[\w]+", query.casefold())
    terms += [query[i:i + 2] for i in range(len(query) - 1) if '\u4e00' <= query[i] <= '\u9fff']
    keyword = {row["chunk_id"]: sum(term in row["text"].casefold() for term in set(terms)) for row in rows}
    kw_order = sorted((row for row in rows if keyword[row["chunk_id"]]), key=lambda row: -keyword[row["chunk_id"]])
    vec_order = sorted((row for row in rows if row["chunk_id"] in semantic), key=lambda row: -semantic[row["chunk_id"]])
    fused = hybrid.rrf([[row["chunk_id"] for row in kw_order], [row["chunk_id"] for row in vec_order]])
    by_id = {row["chunk_id"]: row for row in rows}
    result = []
    for rank, key in enumerate(fused[:limit], 1):
        row = by_id[key]
        result.append({key: value for key, value in row.items() if key not in ("vector", "faiss_id")})
        result[-1].update({"rank": rank, "semantic_score": semantic.get(key), "keyword_matches": keyword[key],
                           "quote": row["text"], "material": "original"})
    if unindexed:
        warnings.append(f"{len(unindexed)} 篇论文尚无已保存的文本块，结果只覆盖已索引论文。")
    return {"status": "partial" if warnings else "completed", "evidence": result, "warnings": warnings,
            "unindexed_paper_ids": unindexed}


def context(papers, chunk_id: str, neighbors: int):
    rows = chunks_for(papers)
    hit = next((row for row in rows if row["chunk_id"] == chunk_id), None)
    if hit is None:
        raise ValueError("文本块不存在或超出当前研究范围")
    same = sorted((row for row in rows if row["paper_id"] == hit["paper_id"]), key=lambda row: row["start_char"])
    pos = next(i for i, row in enumerate(same) if row["chunk_id"] == chunk_id)
    return {"status": "completed", "paper_id": hit["paper_id"], "chunk_id": chunk_id,
            "blocks": [{k: v for k, v in row.items() if k not in ("vector", "faiss_id")}
                       for row in same[max(0, pos - neighbors):pos + neighbors + 1]]}
