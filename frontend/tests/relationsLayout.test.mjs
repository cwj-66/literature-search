import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

function loadModule(relative) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}

const { toggleSelection, parseSeeds, CATEGORY_ORDER, resolveFocus, assignSections, arrowOf, metricText } = await loadModule('../src/relationLayout.ts');

test('toggleSelection appends in order, removes on re-click, allows more than five picks without mutating', () => {
  let selected = [];
  for (const key of ['a', 'b', 'c', 'd', 'e']) selected = toggleSelection(selected, key);
  assert.deepEqual(selected, ['a', 'b', 'c', 'd', 'e']);
  assert.deepEqual(toggleSelection(selected, 'f'), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.deepEqual(selected, ['a', 'b', 'c', 'd', 'e'], 'appending must not mutate');
  const removed = toggleSelection(selected, 'b');
  assert.deepEqual(removed, ['a', 'c', 'd', 'e']);
  assert.deepEqual(selected, ['a', 'b', 'c', 'd', 'e'], 'original array unchanged');
  assert.deepEqual(toggleSelection(removed, 'f'), ['a', 'c', 'd', 'e', 'f']);
});

test('parseSeeds keeps only valid ids, dedupes, preserves order', () => {
  assert.deepEqual(parseSeeds('b,a,b,nope,c', ['a', 'b', 'c']), ['b', 'a', 'c']);
  assert.deepEqual(parseSeeds(null, ['a']), []);
  assert.deepEqual(parseSeeds('', ['a']), []);
  assert.deepEqual(parseSeeds('x,y', ['a']), []);
});

test('resolveFocus honors a valid focus param and falls back to selection order', () => {
  assert.equal(resolveFocus(['a', 'b'], 'b'), 'b');
  assert.equal(resolveFocus(['a', 'b'], 'missing'), 'a');
  assert.equal(resolveFocus(['a'], null), 'a');
  assert.equal(resolveFocus([], null), null);
});

function entry(id) {
  return { id, title: `Paper ${id}`, authors: ['A'], year: 2023, relations: [], categories: [], reasons: [], evidence: [] };
}

test('assignSections puts each paper only in its best-rank category with citation priority on ties', () => {
  const groups = [
    { center_id: 'c', category: 'citation', status: 'completed', matched_count: 2, items: [entry('X'), entry('Y')] },
    { center_id: 'c', category: 'semantic', status: 'partial', matched_count: 2, items: [entry('Y'), entry('Z')] },
    { center_id: 'c', category: 'keyword', status: 'completed', matched_count: 1, items: [entry('X')] },
  ];
  const sections = assignSections(groups);
  assert.deepEqual(sections.map((s) => s.key), [...CATEGORY_ORDER]);
  const byKey = Object.fromEntries(sections.map((s) => [s.key, s]));
  assert.deepEqual(byKey.citation.items.map((i) => i.item.id), ['X'], 'tie at rank 0 resolves to citation');
  assert.deepEqual(byKey.semantic.items.map((i) => i.item.id), ['Y', 'Z'], 'better semantic rank wins for Y');
  assert.deepEqual(byKey.keyword.items.map((i) => i.item.id), []);
  const x = byKey.citation.items[0];
  assert.deepEqual(x.alsoIn, ['keyword'], 'card records its other categories');
  const y = byKey.semantic.items[0];
  assert.deepEqual(y.alsoIn, ['citation']);
  assert.equal(byKey.citation.hidden, 1, 'Y matched citation but is shown in semantic');
  assert.equal(byKey.semantic.hidden, 0);
  assert.equal(byKey.keyword.hidden, 1, 'X matched keyword but is shown in citation');
  assert.equal(byKey.semantic.status, 'partial', 'section status passes through');
  assert.equal(byKey.citation.matchedCount, 2);
});

test('arrowOf only appears for direct in/out evidence', () => {
  assert.equal(arrowOf({ evidence: [{ type: 'out' }] }), 'out');
  assert.equal(arrowOf({ evidence: [{ type: 'in' }] }), 'in');
  assert.equal(arrowOf({ evidence: [{ type: 'out' }, { type: 'in' }] }), 'both');
  assert.equal(arrowOf({ evidence: [{ type: 'bibliographic_coupling' }, { type: 'co_citation' }] }), null);
  assert.equal(arrowOf({ evidence: [] }), null);
  assert.equal(arrowOf({}), null);
});

test('metricText keeps the three category metrics in distinct units', () => {
  assert.equal(metricText({ evidence: [{ type: 'out' }] }, 'citation'), '当前论文引用该文');
  assert.equal(metricText({ evidence: [{ type: 'in' }] }, 'citation'), '该文引用当前论文');
  assert.equal(metricText({ evidence: [{ type: 'out' }, { type: 'in' }] }, 'citation'), '互引');
  assert.equal(metricText({ evidence: [{ type: 'co_citation' }] }, 'citation'), '间接引用（本次抽样）');
  assert.equal(metricText({ semantic_score: 0.8123 }, 'semantic'), '语义 0.81');
  assert.equal(metricText({ shared_keywords: ['a', 'b', 'c', 'd'] }, 'keyword'), '共同词 4 个');
});
