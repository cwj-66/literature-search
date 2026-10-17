import json
import unittest
from unittest.mock import AsyncMock, patch

from app import evidence_analysis as ea, fulltext, llm
from app.schemas import AnalysisClaim, PaperOut


def claim(kind="main_content", text="提出检索方法", quote="We propose a retrieval method.", pid="P0001", **extra):
    return {"kind": kind, "text": text, "evidence": [{"paragraph_id": pid, "quote": quote}], **extra}


class EvidenceAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def prepared(self, text="We propose a retrieval method.", basis="abstract"):
        return fulltext.PreparedText(text, basis, basis, None, None, {"truncated": False})

    def test_invalid_quotes_ids_and_numbers_are_rejected(self):
        source = ea.paragraphs("We propose a retrieval method. Accuracy is 91.2 on benchmark A.")
        claims = [claim(), claim(text="错误证据", quote="unrelated source material"),
                  claim(text="错误编号", pid="P9999"), claim(text="准确率达到99.2%"),
                  claim(text="准确率达到91.2%", quote="Accuracy is 91.2 on benchmark A.")]
        result = ea.grounded([AnalysisClaim.model_validate(c) for c in claims], source, True)
        self.assertEqual([c.text for c in result], ["提出检索方法", "准确率达到91.2%"])

    async def test_semantic_review_rejects_unrelated_claim_even_with_exact_quote(self):
        claims = [AnalysisClaim.model_validate(claim(text="证明在所有语言上有效"))]
        with patch("app.llm.complete", AsyncMock(return_value=json.dumps({"reviews": [{"index": 0, "supported": False}]}))):
            self.assertEqual(await ea.review(claims), [])

    async def test_duplicate_or_missing_review_is_not_accepted(self):
        claims = [AnalysisClaim.model_validate(claim())]
        for reviews in ([], [{"index": 0, "supported": True}] * 2, [{"index": 3, "supported": True}]):
            with patch("app.llm.complete", AsyncMock(return_value=json.dumps({"reviews": reviews}))):
                with self.assertRaises(llm.AnalysisError):
                    await ea.review(claims)

    async def test_abstract_result_is_grounded_and_missing_fields_are_unknown(self):
        output = {"claims": [claim("summary", "本文提出一种检索方法。"), claim("main_content")]}
        responses = [json.dumps(output), json.dumps({"reviews": [{"index": i, "supported": True} for i in range(2)]})]
        with patch("app.llm.complete", AsyncMock(side_effect=responses)) as model:
            result = await llm.analyze(PaperOut(id="test", title="test"), self.prepared())
        self.assertEqual(model.await_count, 2)
        self.assertEqual(result.analysis_basis, "abstract")
        self.assertEqual(result.summary, "本文提出一种检索方法。")
        self.assertEqual(result.experimental_results, ea.UNKNOWN)
        self.assertEqual(result.source_paragraphs[0].text, self.prepared().text)
        self.assertEqual(len(result.content_hash), 64)

    async def test_keywords_alone_do_not_count_as_a_successful_analysis(self):
        outputs = [json.dumps({"claims": [claim("keyword", "检索")]}),
                   json.dumps({"reviews": [{"index": 0, "supported": True}]})]
        with patch("app.llm.complete", AsyncMock(side_effect=outputs)):
            with self.assertRaises(llm.AnalysisError):
                await llm.analyze(PaperOut(id="test", title="test"), self.prepared())

    async def test_long_input_uses_original_paragraphs_through_synthesis(self):
        text = "## Method\n" + ("We propose a retrieval method. " * 650) + "\n\n## Limitations\nOnly English was evaluated."
        prepared = self.prepared(text, "full_text")
        source = ea.paragraphs(text)
        groups = ea.batches(source)
        self.assertGreater(len(groups), 1)
        outputs = []
        for group in groups:
            p = group[-1]
            outputs.append(json.dumps({"claims": [claim("limitation" if p.section == "Limitations" else "main_content",
                "仅评估英语" if p.section == "Limitations" else "研究检索方法", quote=p.text[:100], pid=p.id)]}))
        last = source[-1]
        outputs += [json.dumps({"claims": [claim("limitation", "仅评估英语", quote=last.text, pid=last.id),
                    claim("main_content", "研究检索方法", quote="We propose a retrieval method.")]}),
                    json.dumps({"reviews": [{"index": 0, "supported": True}, {"index": 1, "supported": True}]})]
        with patch("app.llm.complete", AsyncMock(side_effect=outputs)) as model:
            result = await llm.analyze(PaperOut(id="test", title="test"), prepared)
        self.assertEqual(model.await_count, len(groups) + 2)
        self.assertEqual(result.limitations, "仅评估英语")
        self.assertEqual(next(c for c in result.claims if c.kind == "limitation").evidence[0].quote, last.text)

    def test_full_text_preserves_later_sections_and_reports_truncation(self):
        prepared = fulltext.pack([("Method", "a" * 30000), ("Limitations", "limited to English")], "https://arxiv.org/html/test", False, False)
        self.assertIn("limited to English", prepared.text)
        self.assertFalse(prepared.evidence["truncated"])
        prepared = fulltext.pack([("Method", "a" * 100000), ("Limitations", "limited to English")], "https://arxiv.org/html/test", False, False)
        self.assertTrue(prepared.evidence["truncated"])
        self.assertIn("limited to English", prepared.text)

    def test_abstract_claim_cannot_pretend_full_text_was_read(self):
        result = ea.grounded([AnalysisClaim.model_validate(claim(text="根据全文，本文提出检索方法"))], ea.paragraphs(self.prepared().text), True)
        self.assertEqual(result, [])

    def test_missing_summary_reuses_verified_facts_without_model_calls(self):
        item = AnalysisClaim.model_validate(claim()).model_copy(update={"status": "supported"})
        result = ea.ensure_summary([item])
        self.assertEqual(result[0].kind, "summary")
        self.assertIn(item.text, result[0].text)
        self.assertEqual(result[0].evidence, item.evidence)

    def test_html_keeps_abstract_before_first_heading_and_excludes_references(self):
        html = b'<html><article><p>Original abstract text.</p><h2>Method</h2><p>Our method.</p><h2>References</h2><p>Other papers.</p></article></html>'
        sections = fulltext.parse_html(html)
        prepared = fulltext.pack(sections, "https://arxiv.org/html/test", False, False)
        self.assertIn("Original abstract text.", prepared.text)
        self.assertNotIn("Other papers.", prepared.text)


if __name__ == "__main__":
    unittest.main()
