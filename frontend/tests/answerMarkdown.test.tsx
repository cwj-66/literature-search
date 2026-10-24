import React from 'react';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { AnswerMarkdown, EvidenceExcerpt } from '../src/components/AnswerMarkdown';

function render(text: string) {
  return renderToStaticMarkup(<MemoryRouter><AnswerMarkdown text={text}
    evidence={[{ evidence_id:'E0001',paper_id:'p1',quote:'Real source',material:'original',basis:'full_text' },
      { evidence_id:'E0002',paper_id:'p1',quote:'Another source',material:'original',basis:'abstract' }]}
    titles={new Map([['p1','Paper One']])} paths={new Map([['p1','/w/test/paper/p1']])} from='/w/test/studio' /></MemoryRouter>);
}
test('GFM tables, emphasis, formulas and citations render together',()=>{
  const html=render('### 方法\n\n| 阶段 | 机制 |\n| --- | --- |\n| 排序 | **打分** $k \\ll N$ [E0001] |');
  assert.match(html,/<table>/);assert.match(html,/<th>阶段<\/th>/);
  assert.match(html,/<strong>打分<\/strong>/);assert.match(html,/class="katex"/);
  assert.match(html,/<button[^>]*aria-haspopup="dialog"/);assert.match(html,/来源 1/);
  assert.doesNotMatch(html,/href="\/w\/test\/paper\/p1"/);
  assert.match(html,/class="answer-table-scroll"/);
});

test('adjacent citations combine and deduplicate without merging separate claims',()=>{
  const html=render('共同依据[E0001] [E0002][E0001]。另一结论[E0002]。未知项[E9999]');
  assert.equal((html.match(/class="cite-mark"/g)||[]).length,2);
  assert.match(html,/来源 1、2/);assert.doesNotMatch(html,/E9999/);
});

test('excerpt preserves exact source text and location and labels abstract honestly',()=>{
  const html=renderToStaticMarkup(<MemoryRouter><EvidenceExcerpt label="来源 2" title="Paper One" path="/paper"
    source={{evidence_id:'E0002',quote:'Original sentence.\n<script>not executable</script>',basis:'abstract',section_path:['Methods','Annotation'],page_start:3,page_end:4}} /></MemoryRouter>);
  assert.match(html,/摘要原文/);assert.match(html,/Methods › Annotation/);assert.match(html,/第 3–4 页/);
  assert.match(html,/Original sentence.\n&lt;script&gt;not executable&lt;\/script&gt;/);
  assert.match(html,/查看完整论文/);assert.doesNotMatch(html,/<script>/);
});

test('historical missing excerpts and model summaries are never presented as original full text',()=>{
  const html=renderToStaticMarkup(<EvidenceExcerpt label="来源 1" source={{evidence_id:'E0001',basis:'full_text',material:'model_summary'}} />);
  assert.match(html,/模型摘要（非原文）/);assert.match(html,/没有保存原文片段/);assert.doesNotMatch(html,/正文原文/);
});

test('abstract section retrieved from a full document is still identified as abstract',()=>{
  const html=renderToStaticMarkup(<EvidenceExcerpt label="来源 1" source={{evidence_id:'E0001',basis:'full_text',section_path:['Paper','Abstract'],quote:'Abstract text'}} />);
  assert.match(html,/摘要原文（取自全文）/);assert.doesNotMatch(html,/正文原文/);
});
test('unapproved citations, raw HTML and dangerous links are not executable',()=>{
  const html=render('[E9999]\n\n<script>alert(1)</script>\n\n[bad](javascript:alert%281%29)\n\n![tracking](https://example.test/pixel)');
  assert.doesNotMatch(html,/E9999|<script|href="javascript:|<img/);
});
test('citations inside code remain literal code',()=>{
  const html=render('`[E0001]` and a real citation [E0001]');
  assert.match(html,/<code>\[E0001\]<\/code>/);
  assert.equal((html.match(/class="cite-mark"/g)||[]).length,1);
});
