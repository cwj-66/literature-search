import json
import unittest
from unittest.mock import AsyncMock, patch

from app import attribution as att
from app.evidence_analysis import UNKNOWN
from app.schemas import AnalysisClaim, AnalysisFields, PaperOut, SourceParagraph


def fields(**extra):
    return AnalysisFields(
        research_problem="降低幻觉", main_content="检索加生成", technical_method="证据约束解码",
        innovations=["引用必须回到原文"], experimental_results="在基准A上准确率91.2%",
        limitations="仅英文", keywords=["RAG"], analysis_basis="abstract",
        claims=[
            AnalysisClaim(kind="research_problem", text="降低幻觉", evidence=[{"paragraph_id": "P0001", "quote": "We reduce hallucination."}], status="supported"),
            AnalysisClaim(kind="evaluation", text="准确率91.2%", dataset="基准A", metric="准确率", result="91.2%",
                          evidence=[{"paragraph_id": "P0002", "quote": "Accuracy is 91.2 on benchmark A."}], status="supported"),
        ],
        **extra,
    )


class AttributionTests(unittest.IsolatedAsyncioTestCase):
    def test_quote_must_occur_in_source(self):
        source = [SourceParagraph(id="P0001", section="方法", text="We reduce hallucination with citations.")]
        self.assertEqual(att.locate_quote("We reduce hallucination", source).id, "P0001")
        self.assertIsNone(att.locate_quote("totally unrelated claim text", source))

    def test_invalid_evidence_ids_are_stripped(self):
        self.assertEqual(att.drop_invalid_ids("结论[E0001]和E0099", {"E0001"}), "结论[E0001]和")

    def test_comparison_marks_missing_and_incomparable(self):
        left = PaperOut(id="p1", title="A", sources=["arxiv"])
        right = PaperOut(id="p2", title="B", sources=["arxiv"])
        other = fields()
        other.claims[1] = other.claims[1].model_copy(update={"dataset": "基准B", "metric": "F1"})
        table = att.comparison_table([(left, fields()), (right, other)])
        self.assertTrue(any("不可直接排名" in note for note in table["notes"]))
        empty = AnalysisFields(
            research_problem=UNKNOWN, main_content=UNKNOWN, technical_method=UNKNOWN,
            innovations=[UNKNOWN], experimental_results=UNKNOWN, limitations=UNKNOWN,
            keywords=[UNKNOWN], analysis_basis="abstract",
        )
        missing = att.comparison_table([(left, empty)])
        cell = next(c for c in missing["rows"][0]["cells"] if c["dimension"] == "研究问题")
        self.assertTrue(cell["missing"])

    async def test_judge_rejects_ids_outside_evidence_set(self):
        evidence = [att.EvidenceItem(evidence_id="E0001", paper_id="p1", quote="We reduce hallucination.")]
        claims = att.bind_claim_ids([AnalysisClaim(kind="main_content", text="降低幻觉", evidence=[], status="not_stated")])
        payload = {"reviews": [{"claim_id": claims[0]["claim_id"], "evidence_ids": ["E0001", "E9999"], "verdict": "supported", "note": ""}]}
        with patch("app.llm.complete", AsyncMock(return_value=json.dumps(payload))):
            reviews = await att.judge_claims("问题", claims, evidence)
        self.assertEqual(reviews[0].evidence_ids, ["E0001"])


if __name__ == "__main__":
    unittest.main()
