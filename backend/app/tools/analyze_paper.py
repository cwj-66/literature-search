"""analyze_paper：agent 与用户共用的论文分析工具。

有需要分析论文时调用。默认提取五项：研究内容、技术方案、创新点与主要贡献、实验结果、局限性。
每项返回结构化内容、原文证据（章节；有页码时带页码）、未找到的信息，以及处理状态。
提供 workspace_id 时只分析文献库里已有的论文，不向外检索。
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.infrastructure import llm
from app.schemas import AnalysisClaim, AnalysisFields
from app.services.analysis import analysis, analysis_store, attribution, fulltext
from app.services.papers import library

UNKNOWN = "论文未明确说明"
MAX_PAPERS = 5

DEFAULT_FIELDS: tuple[dict, ...] = (
    {"id": "research_content", "label": "研究内容", "aspects": ("研究背景", "核心问题", "研究目标")},
    {"id": "technical_scheme", "label": "技术方案", "aspects": ("方法原理", "技术流程", "关键模块")},
    {"id": "contributions", "label": "创新点与主要贡献", "aspects": ("作者声称的贡献", "相对已有方法的改进")},
    {"id": "experiments", "label": "实验结果", "aspects": ("数据集", "评价指标", "主要结果", "实验条件")},
    {"id": "limitations", "label": "局限性", "aspects": ("作者明确指出的局限", "有证据支持的潜在局限")},
)
FIELD_BY_ID = {item["id"]: item for item in DEFAULT_FIELDS}

class AnalyzePaperRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=MAX_PAPERS)
    fields: list[str] | None = None
    depth: Literal["standard", "deep"] = "standard"
    workspace_id: str | None = Field(None, max_length=80)


TOOL = {
    "name": "analyze_paper",
    "description": "有需要分析论文时使用。提取结构化结论，并附上原文证据、章节或页码，以及未找到的信息。",
    "parameters": {
        "paper_ids": "要分析的论文 ID，可以是一篇或多篇",
        "fields": "要提取的字段，默认五项：" + "、".join(item["label"] for item in DEFAULT_FIELDS),
        "depth": "分析深度：standard 为标准分析，deep 为深度分析",
        "workspace_id": "所属工作区。提供后只使用文献库中的论文，用于限制数据范围",
    },
}


def normalize_fields(fields: list[str] | None) -> list[str]:
    if fields is None:
        return [item["id"] for item in DEFAULT_FIELDS]
    if not fields:
        raise ValueError("请至少选择一个分析字段")
    unknown = [item for item in fields if item not in FIELD_BY_ID]
    if unknown:
        raise ValueError("未知分析字段：" + "、".join(unknown))
    seen = []
    for item in fields:
        if item not in seen:
            seen.append(item)
    return seen


def _useful(value: str | None) -> str:
    text = (value or "").strip()
    return "" if not text or text == UNKNOWN else text


def _claims(fields: AnalysisFields, kinds: set[str]) -> list[AnalysisClaim]:
    return [claim for claim in fields.claims if claim.kind in kinds and _useful(claim.text)]


def _evidence(claims: list[AnalysisClaim], fields: AnalysisFields) -> list[dict]:
    sections = {item.id: item.section for item in fields.source_paragraphs}
    seen, result = set(), []
    for claim in claims:
        for ref in claim.evidence:
            quote = ref.quote.strip()
            key = (ref.paragraph_id, quote)
            if not quote or key in seen:
                continue
            seen.add(key)
            section = sections.get(ref.paragraph_id) or None
            result.append({"quote": quote, "section": section, "page": None, "paragraph_id": ref.paragraph_id})
    return result


def _field(field_id: str, content: str, evidence: list[dict], covered: set[str]) -> dict:
    spec = FIELD_BY_ID[field_id]
    missing = [name for name in spec["aspects"] if name not in covered]
    if content and not evidence:
        missing.append("原文证据")
    if not content:
        status = "not_found"
        missing = list(spec["aspects"])
    elif missing:
        status = "partial"
    else:
        status = "found"
    return {"field": field_id, "label": spec["label"], "content": content or None, "evidence": evidence, "missing": missing, "status": status}


def _join(parts: list[str]) -> str:
    seen, result = set(), []
    for part in parts:
        text = _useful(part)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return "\n".join(result)


def project_fields(fields: AnalysisFields, requested: list[str]) -> list[dict]:
    problems = _claims(fields, {"research_problem"})
    goals = _claims(fields, {"main_content"})
    methods = _claims(fields, {"technical_method"})
    steps = _claims(fields, {"method_step"})
    innovations = _claims(fields, {"innovation"})
    evaluations = _claims(fields, {"evaluation"})
    limitations = _claims(fields, {"limitation"})
    research = set()
    if problems or _useful(fields.research_problem):
        research.update({"研究背景", "核心问题"})
    if goals or _useful(fields.main_content):
        research.add("研究目标")
    method_text = " ".join(claim.text for claim in methods + steps)
    technical = set()
    if methods or _useful(fields.technical_method):
        technical.add("方法原理")
    if steps:
        technical.add("技术流程")
    if re.search(r"模块|组件|子模块", method_text):
        technical.add("关键模块")
    contribution_text = [claim.text for claim in innovations] or [_useful(item) for item in fields.innovations]
    contribution_text = [item for item in contribution_text if item]
    contributions = {"作者声称的贡献"} if contribution_text else set()
    if any(re.search(r"相比|相较|改进|优于|不同于|相对", item) for item in contribution_text):
        contributions.add("相对已有方法的改进")
    slots = {
        "数据集": [claim.dataset or "" for claim in evaluations if _useful(claim.dataset)],
        "评价指标": [claim.metric or "" for claim in evaluations if _useful(claim.metric)],
        "主要结果": [claim.result or "" for claim in evaluations if _useful(claim.result)],
        "实验条件": [claim.conditions or "" for claim in evaluations if _useful(claim.conditions)],
    }
    if not slots["主要结果"] and (evaluations or _useful(fields.experimental_results)):
        slots["主要结果"] = [claim.text for claim in evaluations] or [_useful(fields.experimental_results)]
    experiments = {name for name, values in slots.items() if any(_useful(value) for value in values)}
    limit_text = [claim.text for claim in limitations] or ([_useful(fields.limitations)] if _useful(fields.limitations) else [])
    limited = {"作者明确指出的局限"} if limit_text else set()
    if any(re.search(r"潜在|可能的局限|可能存在局限", item) for item in limit_text):
        limited.add("有证据支持的潜在局限")
    experiment_lines = [f"{name}：{'；'.join(_useful(value) for value in values if _useful(value))}" for name, values in slots.items() if name in experiments]
    built = {
        "research_content": _field("research_content", _join([fields.research_problem, fields.main_content, *(c.text for c in problems + goals)]), _evidence(problems + goals, fields), research),
        "technical_scheme": _field("technical_scheme", _join([fields.technical_method, *(c.text for c in methods + steps)]), _evidence(methods + steps, fields), technical),
        "contributions": _field("contributions", _join(contribution_text), _evidence(innovations, fields), contributions),
        "experiments": _field("experiments", _join(experiment_lines or [fields.experimental_results]), _evidence(evaluations, fields), experiments),
        "limitations": _field("limitations", _join(limit_text or [fields.limitations]), _evidence(limitations, fields), limited),
    }
    return [built[item] for item in requested]


def _paper_status(items: list[dict], requested_deep: bool, applied: str) -> str:
    if items and all(item["status"] == "found" for item in items) and not (requested_deep and applied != "deep"):
        return "completed"
    return "partial"


def _closed(paper_id: str, status: str, message: str, depth: str) -> dict:
    return {
        "paper_id": paper_id, "title": None, "status": status, "depth": depth, "depth_applied": depth,
        "basis": None, "message": message, "fields": [], "evidence_ids": [],
    }


async def _analyze_one(paper_id: str, requested: list[str], depth: str, workspace_id: str | None, given, prepared, evidence_start: int) -> tuple[dict, int]:
    try:
        target = analysis.resolve_target(paper_id)
    except analysis.ResolveError as exc:
        return _closed(paper_id, "not_found", exc.message, depth), 0
    stored = library.get_paper(target.local_id or paper_id) or library.get_paper(paper_id)
    supplied = (given or {}).get(paper_id)
    if workspace_id and stored is None:
        return _closed(paper_id, "out_of_scope", "这篇论文不在文献库中。工作区分析只使用已收藏的论文，不会向外检索。", depth), 0
    paper = stored or supplied
    if paper is None:
        return _closed(paper_id, "not_found", "没有找到这篇论文，请先加入文献库。", depth), 0
    ready = (prepared or {}).get(paper_id)
    reused = None
    if ready is None:
        try:
            _, reused = analysis_store.get_state(target.aliases, target.local_id)
        except (ValueError, OSError):
            reused = None
    fields = None
    basis = None
    message = None
    text = ready
    if text is None and reused and (depth == "standard" or reused.get("analysis_basis") == "full_text"):
        try:
            fields = AnalysisFields.model_validate(reused)
            basis = reused.get("analysis_basis") or fields.analysis_basis
        except ValueError:
            fields = None
    llm_calls = 0
    if fields is None:
        if text is None:
            text = await fulltext.prepare(paper) if depth == "deep" else fulltext.abstract_text(paper, "标准分析使用摘要")
        if text is None:
            return {**_closed(paper_id, "failed", "没有可用的摘要或全文，无法分析。", depth), "title": paper.title}, 0
        try:
            fields = await llm.analyze(paper, text)
        except llm.AnalysisError as exc:
            return {**_closed(paper_id, "failed", exc.message, depth), "title": paper.title}, 0
        llm_calls = 1
        basis = text.basis
        if depth == "deep" and text.basis != "full_text":
            message = "未取得全文，已按摘要分析。"
    projected = project_fields(fields, requested)
    applied = "deep" if basis == "full_text" else "standard"
    status = _paper_status(projected, depth == "deep", applied)
    if message and status == "completed":
        status = "partial"
    evidence = attribution.evidence_from_claims(paper.id, fields, start=evidence_start)
    return {
        "paper_id": paper_id, "title": paper.title, "status": status, "depth": depth, "depth_applied": applied,
        "basis": basis, "message": message, "fields": projected, "evidence_ids": [item.evidence_id for item in evidence],
    }, llm_calls


async def analyze_paper(paper_ids: list[str], fields: list[str] | None = None, depth: str = "standard", workspace_id: str | None = None, papers: dict | None = None, prepared: dict | None = None, evidence_start: int = 1) -> dict:
    if depth not in ("standard", "deep"):
        raise ValueError("分析深度只能是 standard 或 deep")
    requested = normalize_fields(fields)
    ids = []
    for paper_id in paper_ids:
        paper_id = paper_id.strip()
        if paper_id and paper_id not in ids:
            ids.append(paper_id)
    if not ids:
        raise ValueError("请提供至少一篇论文")
    if len(ids) > MAX_PAPERS:
        raise ValueError(f"一次最多分析 {MAX_PAPERS} 篇论文")
    reports, calls, start = [], 0, evidence_start
    for paper_id in ids:
        report, used = await _analyze_one(paper_id, requested, depth, workspace_id, papers, prepared, start)
        reports.append(report)
        calls += used
        start += len(report.get("evidence_ids") or [])
    statuses = [item["status"] for item in reports]
    if statuses and all(item == "completed" for item in statuses):
        overall = "completed"
    elif statuses and all(item in {"failed", "not_found", "out_of_scope"} for item in statuses):
        overall = "failed"
    else:
        overall = "partial"
    return {
        "tool": "analyze_paper", "workspace_id": workspace_id, "depth": depth, "fields": requested,
        "status": overall, "llm_calls": calls, "papers": reports,
    }
