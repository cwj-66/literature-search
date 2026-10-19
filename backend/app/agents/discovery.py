"""Bibliographic request constraints and output selection; no model or storage access."""
import re

NUMBER = r"[0-9一二两三四五六七八九十百]{1,3}"
QUANTITY = re.compile(rf"(?<![0-9])({NUMBER})\s*(?:篇(?:论文|文献|文章)?|(?:个|份|本)(?=[\s\S]{{0,45}}(?:论文|文献|文章)))|\btop\s+(\d{{1,3}})\b|\b(?:find|recommend|show|give)(?:\s+me)?\s+(\d{{1,3}})\s+(?:\w+\s+){{0,5}}(?:papers?|articles?|studies)\b", re.I)
DETAILS = re.compile(r"分析|比较|对比|梳理|总结|评估|解读|实验结果|实现细节|(?:写|做|帮我).{0,6}综述|研究脉络|analy[sz]e|compare|summari[sz]e|explain|how\b|^review\b", re.I)
FIND = re.compile(r"找|搜索|检索|推荐|寻找|论文|文献|文章|\b(?:find|search|recommend|papers?|articles?)\b", re.I)
RAG = ["RAG", "retrieval-augmented generation", "retrieval augmented generation", "检索增强生成"]
EMBEDDING = ["text embedding", "text embeddings", "sentence embedding", "sentence embeddings", "dense embedding", "dense embeddings", "embedding model", "embedding models", "BGE", "E5", "向量模型", "向量大模型", "文本向量"]
APPLICATION = ["application", "applications", "applied", "deployment", "deployed", "real-world", "real world", "clinical", "healthcare", "medical", "education", "educational", "industrial", "industry", "legal", "finance", "financial", "nutrigenetics", "应用", "落地"]
FACETS = [(r"应用|落地|\bapplications?\b", APPLICATION),
          (r"医疗|医学|\b(?:medical|clinical|healthcare)\b", ["medical", "clinical", "healthcare", "medicine", "医疗", "医学"]),
          (r"教育|\beducation\b", ["education", "educational", "teaching", "learning support", "教育"])]

EARLY_HINT = re.compile(r"早期|最早|最先|最初|奠基|经典|开山")
RECENT_HINT = re.compile(r"最新|新近|最近")
EN_TEMPORAL_WORDS = {"early", "earliest", "foundational", "seminal", "original", "pioneering",
                     "first", "recent", "latest", "newest", "novel"}


def is_temporal_term(term) -> bool:
    text = str(term or "").strip()
    if not text:
        return False
    if text.casefold() in EN_TEMPORAL_WORDS:
        return True
    return bool(EARLY_HINT.search(text) or RECENT_HINT.search(text))


def temporal_of(goal) -> str | None:
    text = goal or ""
    if EARLY_HINT.search(text):
        return "early"
    if RECENT_HINT.search(text):
        return "recent"
    lowered = text.casefold()
    if any(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", lowered) for word in
           ("early", "earliest", "foundational", "seminal", "pioneering")):
        return "early"
    if any(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", lowered) for word in
           ("latest", "newest", "recent")):
        return "recent"
    return None


def temporal_free_groups(groups) -> list:
    kept = []
    for group in groups or []:
        terms = [term for term in group if not is_temporal_term(term)]
        if terms:
            kept.append(terms)
    return kept


def strip_temporal(query: str) -> str:
    """移除检索式中的时间限定词；布尔视图查询整组剔除，纯文本逐词剔除。"""
    text = str(query or "").strip()
    if not text:
        return ""
    from app.services.retrieval.search import parse_view_groups
    groups = parse_view_groups(text)
    if groups:
        kept = temporal_free_groups(groups)
        if not kept:
            return ""
        return " AND ".join("(" + " OR ".join(f'"{term}"' for term in group) + ")" for group in kept)
    cleaned = EARLY_HINT.sub(" ", text)
    cleaned = RECENT_HINT.sub(" ", cleaned)
    for word in EN_TEMPORAL_WORDS:
        cleaned = re.sub(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", " ", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip(" ,;、") or text


def number(text):
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" in text:
        left, right = text.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0)
    return 100 if text == "一百" else digits.get(text)


def requested_count(goal):
    match = QUANTITY.search(goal or "")
    count = number(next(value for value in match.groups() if value is not None)) if match else None
    return count if count and 1 <= count <= 120 else None


def bibliography_goal(goal):
    return bool(FIND.search(goal or "") and not DETAILS.search(goal or ""))


def contains(text, term):
    text = re.sub(r"[‐‑–—]", "-", text)
    term = str(term).strip().casefold()
    if not term:
        return False
    # Acronyms must not match substrings such as "rag" in "fragment".
    if term.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text.casefold()))
    return term in text.casefold()


def required_groups(goal, plan=None):
    groups = []
    if any(contains(goal, term) for term in RAG):
        groups.append(RAG)
    if re.search(r"向量(?:大)?模型|文本向量|\b(?:text|sentence|dense) embeddings?\b|\bembedding models?\b", goal, re.I):
        groups.append(EMBEDDING)
    for pattern, synonyms in FACETS:
        if re.search(pattern, goal, re.I):
            groups.append(synonyms)
    for group in (plan or {}).get("must_have_facets") or []:
        if isinstance(group, list) and any(isinstance(term, str) and term.strip() for term in group):
            clean = [term for term in group if isinstance(term, str) and term.strip()]
            for aliases in (plan or {}).get("likely_synonyms") or []:
                if isinstance(aliases, list) and any(contains(term, alias) or contains(alias, term) for term in clean for alias in aliases if isinstance(alias, str)):
                    clean = list(dict.fromkeys([*clean, *[alias for alias in aliases if isinstance(alias, str)]]))
            # Expand known equivalences rather than demanding a literal word like "application".
            if any(contains(" ".join(clean), term) for term in RAG):
                clean = RAG
            elif any(re.fullmatch(r"applications?|应用|落地", term, re.I) for term in clean):
                clean = APPLICATION
            groups.append(clean)
    return groups


def output_papers(state):
    ids = state.get("recommended_ids")
    if ids is None:
        return []
    by_id = {p["id"]: p for p in state.get("papers") or []}
    return [by_id[pid] for pid in ids if pid in by_id]


def protect_query(query, goal, plan=None):
    """Retain required concepts in every search view and refinement."""
    groups = required_groups(goal, plan)
    if not groups:
        return query
    if re.search(r"[\u4e00-\u9fff]", query):
        # Known bilingual facets are a deterministic fallback when translation failed.
        query = ""
    missing = [group for group in groups if not any(contains(query, term) for term in group)]
    compiled = ["(" + " OR ".join('"' + term.replace('"', '') + '"' for term in group[:3]) + ")" for group in missing]
    protected = " AND ".join([*([query] if query else []), *dict.fromkeys(compiled)])
    if any(contains(goal, term) for term in RAG):
        # RAG is also an immunology acronym. Search the expanded method name;
        # a bare OR "RAG" otherwise admits unrelated gene-deficiency papers.
        protected = re.sub(r'"RAG"', '"retrieval-augmented generation"', protected, flags=re.I)
    from app.services.retrieval.search import parse_view_groups
    parsed = parse_view_groups(protected)
    if parsed:
        # Keep all AND facets while shortening oversized synonym lists. Never
        # slice a compiled query in the middle of its quotes or parentheses.
        parsed = [list({term.casefold(): term for term in group}.values()) for group in parsed]
        def render():
            return " AND ".join("(" + " OR ".join('"' + term + '"' for term in group) + ")" for group in parsed)
        while len(render()) > 300 and any(len(group) > 1 for group in parsed):
            max((group for group in parsed if len(group) > 1), key=lambda group: sum(map(len, group))).pop()
        protected = render()
    return protected


def select_bibliography(state):
    """Require all hard facets and at least one topic signal; never fill with unrelated hits."""
    goal = state.get("goal") or ""
    plan = state.get("search_plan") or {}
    groups = required_groups(goal, plan)
    core = [str(term) for term in plan.get("core_concepts") or [] if isinstance(term, str)]
    for term in core:
        if not any(contains(term, alias) or contains(alias, term) for group in groups for alias in group):
            aliases = next((row for row in plan.get("likely_synonyms") or [] if isinstance(row, list) and term in row), [term])
            groups.append(aliases)
    if not groups:
        from app.services.retrieval.search import parse_view_groups
        queries = state.get("queries") or [goal]
        groups = parse_view_groups(queries[min(state.get("query_index", 0), len(queries) - 1)]) or []
    if not groups:
        stopwords = {"find", "papers", "paper", "search", "recommend", "about", "please", "the", "and", "for", "top", "show", "give"}
        terms = [w for w in re.findall(r"[a-zA-Z]{3,}", goal) if w.casefold() not in stopwords]
        groups = [[term] for term in terms]
    groups = temporal_free_groups(groups)
    found, reasons = [], {}
    for paper in state.get("papers") or []:
        if paper["id"] in (state.get("workflow_local_ids") or []):
            continue
        if state.get("year_from") and (paper.get("year") is None or paper["year"] < state["year_from"]):
            continue
        if state.get("year_to") and (paper.get("year") is None or paper["year"] > state["year_to"]):
            continue
        text = f"{paper.get('title') or ''} {paper.get('abstract') or ''}"
        if any(contains(goal, term) for term in RAG) and not re.search(r"retriev\w*|language models?|\bLLMs?\b|generative (?:AI|models?)|检索增强", text, re.I):
            continue
        hits = [next((term for term in group if contains(text, term)), None) for group in groups]
        if not groups or not all(hits):
            continue
        if any(contains(text, term) for term in plan.get("exclusions") or []):
            continue
        # A model's explicit low relevance score is also a rejection signal.
        if paper.get("rank_score") is not None and paper["rank_score"] < 7:
            continue
        found.append(paper)
        title_hits = [term for term in hits if contains(paper.get("title") or "", term)]
        reasons[paper["id"]] = "标题" + ("与摘要" if len(title_hits) != len(hits) else "") + "匹配：" + "、".join(dict.fromkeys(hits))
    limit = state.get("result_limit") or 5
    chosen = found[:limit]
    return [paper["id"] for paper in chosen], {paper["id"]: reasons[paper["id"]] for paper in chosen}, len(found)


def select_research(state):
    """Keep the final source set consistent with actually read or cited papers."""
    used = {item.get("paper_id") for item in (state.get("evidence_items") or {}).values() if item.get("material") in {"abstract", "original"}}
    used.update(pid for pid, row in (state.get("workflow_reads") or {}).items() if row.get("basis") in {"abstract", "full_text"})
    return [p["id"] for p in state.get("papers") or [] if p["id"] in used][:state.get("result_limit") or 5]
