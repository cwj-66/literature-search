import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/researchPresentation.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { researchResults, researchSummary, researchHistory } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
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
