import unittest
from unittest.mock import AsyncMock, patch

from app.services.analysis import analysis
from app.tools import analyze_paper as analyze_tool
from app.schemas import AnalysisClaim, AnalysisFields, LocalPaperOut, SourceParagraph


def claim(kind, text, quote, pid, **extra):
    return AnalysisClaim(kind=kind, text=text, evidence=[{"paragraph_id": pid, "quote": quote}], status="supported", **extra)


def sample_fields() -> AnalysisFields:
    return AnalysisFields(
        research_problem="背景是检索不可靠，核心问题是幻觉。",
        main_content="研究目标是约束生成。",
        technical_method="用检索证据约束解码。",
        innovations=["作者声称引入自检。"],
        experimental_results="准确率提升。",
        limitations="只评估了英语。",
        keywords=["RAG"],
        analysis_basis="full_text",
        claims=[
            claim("research_problem", "背景是检索不可靠，核心问题是幻觉。", "Retrieval can be unreliable.", "P0001"),
            claim("main_content", "研究目标是约束生成。", "The goal is constrained generation.", "P0001"),
            claim("technical_method", "用检索证据约束解码。", "We constrain decoding.", "P0002"),
            claim("method_step", "先检索再生成。", "Retrieve then generate.", "P0002"),
            claim("innovation", "作者声称引入自检。", "We propose self-check.", "P0003"),
            claim("evaluation", "准确率提升。", "On NQ accuracy improves.", "P0004", dataset="NQ", metric="accuracy", result="提升 3 个点", conditions="英文测试集"),
            claim("limitation", "只评估了英语。", "We only evaluate English.", "P0005"),
        ],
        source_paragraphs=[
            SourceParagraph(id="P0001", section="引言", text="Retrieval can be unreliable. The goal is constrained generation."),
            SourceParagraph(id="P0002", section="方法", text="We constrain decoding. Retrieve then generate."),
            SourceParagraph(id="P0003", section="贡献", text="We propose self-check."),
            SourceParagraph(id="P0004", section="实验", text="On NQ accuracy improves."),
            SourceParagraph(id="P0005", section="局限性", text="We only evaluate English."),
        ],
    )


class ProjectTests(unittest.TestCase):
    def test_default_fields_keep_evidence_and_name_gaps(self):
        projected = analyze_tool.project_fields(sample_fields(), analyze_tool.normalize_fields(None))
        by_id = {item["field"]: item for item in projected}
        self.assertEqual([item["label"] for item in projected], ["研究内容", "技术方案", "创新点与主要贡献", "实验结果", "局限性"])
        self.assertEqual(by_id["research_content"]["status"], "found")
        self.assertEqual(by_id["research_content"]["evidence"][0]["section"], "引言")
        self.assertIsNone(by_id["research_content"]["evidence"][0]["page"])
        self.assertIn("关键模块", by_id["technical_scheme"]["missing"])
        self.assertEqual(by_id["technical_scheme"]["status"], "partial")
        self.assertIn("相对已有方法的改进", by_id["contributions"]["missing"])
        self.assertEqual(by_id["experiments"]["status"], "found")
        self.assertIn("数据集：NQ", by_id["experiments"]["content"])
        self.assertIn("有证据支持的潜在局限", by_id["limitations"]["missing"])
        self.assertNotIn("作者明确指出的局限", by_id["limitations"]["missing"])
        self.assertIn("只评估了英语", by_id["limitations"]["content"])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze_tool.normalize_fields(["keywords"])


class ScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_does_not_fetch_outside_library(self):
        target = analysis.Target("arxiv:1", {"arxiv:1"}, None)
        with patch("app.tools.analyze_paper.analysis.resolve_target", return_value=target), \
             patch("app.tools.analyze_paper.library.get_paper", return_value=None), \
             patch("app.tools.analyze_paper.llm.analyze", new=AsyncMock(side_effect=AssertionError("should not analyze"))):
            result = await analyze_tool.analyze_paper(["arxiv:1"], workspace_id="demo")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["papers"][0]["status"], "out_of_scope")
        self.assertEqual(result["workspace_id"], "demo")

    async def test_standard_reuses_stored_analysis(self):
        paper = LocalPaperOut(id="arxiv:1", local_id="loc", imported_at="2026-01-01T00:00:00+00:00", title="Paper", sources=["arxiv"], abstract="Abstract.")
        stored = sample_fields().model_dump()
        stored["analysis_basis"] = "full_text"
        target = analysis.Target("loc", {"loc", "arxiv:1"}, "loc")
        with patch("app.tools.analyze_paper.analysis.resolve_target", return_value=target), \
             patch("app.tools.analyze_paper.library.get_paper", return_value=paper), \
             patch("app.tools.analyze_paper.analysis_store.get_state", return_value=(None, stored)), \
             patch("app.tools.analyze_paper.llm.analyze", new=AsyncMock(side_effect=AssertionError("should reuse"))):
            result = await analyze_tool.analyze_paper(["arxiv:1"], depth="standard", workspace_id="demo")
        self.assertEqual(result["llm_calls"], 0)
        self.assertEqual(result["papers"][0]["basis"], "full_text")
        self.assertEqual(result["papers"][0]["fields"][0]["label"], "研究内容")
