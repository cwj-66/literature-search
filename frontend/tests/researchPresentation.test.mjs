import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/researchPresentation.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { researchResults, researchSummary, researchHistory, researchHasActivity } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const pool = Array.from({ length: 24 }, (_, i) => ({ id: `p:${i}`, title: `Paper number ${i}.` }));

test('24 candidates do not become 24 recommended cards', () => {
  const turn = { task: { recommended: pool.slice(5, 8), candidates: pool.filter((_, i) => i < 5 || i >= 8), candidate_count: 24 } };
  const result = researchResults(turn);
  assert.equal(result.recommended.length, 3);
  assert.equal(result.candidates.length, 21);
  assert.equal(result.recommended[0].id, 'p:5');
});

test('historical snapshots only recover explicitly named recommendations', () => {
  const result = researchResults({ papers: pool, task: { answer: 'Recommended: Paper number 5. and Paper number 17.' } });
  assert.deepEqual(result.recommended.map(p => p.id), ['p:5', 'p:17']);
  assert.equal(result.candidates.length, 22);
  assert.equal(researchResults({ papers: pool }).recommended.length, 0);
});

test('progress and follow-up context use the chosen set, not the pool', () => {
  const turn = { question: 'find three papers', task: { answer: '推荐 3 篇', search_count: 1, recommended: pool.slice(5, 8), candidates: pool.filter((_, i) => i < 5 || i >= 8), reading_status: {} } };
  assert.equal(researchSummary(turn), '检索 1 次 · 推荐 3 篇');
  const history = researchHistory(turn);
  assert.match(history, /p:5/);
  assert.match(history, /p:7/);
  assert.doesNotMatch(history, /p:0：/);
  assert.doesNotMatch(history, /p:23/);
});

test('a conversational reply has no research activity or zero-paper report', () => {
  const turn = { question:'是找不到吗', task:{answer:'这次没找够，不代表不存在。',search_count:0,reading_status:{},events:[{step:'route'},{step:'direct'},{step:'stop'}]} };
  assert.equal(researchHasActivity(turn), false);
  assert.equal(researchSummary(turn), '已回复');
  assert.doesNotMatch(researchHistory(turn), /上轮任务记录/);
});

test('follow-up history distinguishes a partial source failure from no matching results', () => {
  const turn = {question:'找5篇向量模型论文',task:{goal:'找5篇向量模型论文',answer:'找到2篇',recommended:pool.slice(0,2),result_limit:5,queries:['text embedding'],search_count:1,candidate_count:20,source_errors:{semantic_scholar:'timeout'},stop_reason:'书目筛选完成'}};
  const history = researchHistory(turn);
  assert.match(history, /"requested":5/);
  assert.match(history, /"recommended":2/);
  assert.match(history, /semantic_scholar.*timeout/);
  assert.match(history, /text embedding/);
});

test('follow-up discussion receives the saved reason and actual abstract', () => {
  const turn = { question: 'find image generation papers', task: { search_count: 1, queries: ['retrieved patches'],
    recommended: [{id:'arxiv:synthetic',title:'Synthetic',abstract:'Retrieves patches during autoregressive generation.',year:2024,url:'https://example.test/paper'}],
    recommendation_reasons: {'arxiv:synthetic':'摘要明确描述自回归生成中的patch检索'},
    source_status:[{source:'semantic_scholar',status:'rate_limited',http_status:429}], source_warnings:['OpenAlex fallback'] } };
  const history = researchHistory(turn);
  assert.match(history, /自回归生成中的patch检索/);
  assert.match(history, /Retrieves patches during autoregressive/);
  assert.match(history, /"http_status":429/);
  assert.match(history, /OpenAlex fallback/);
});
