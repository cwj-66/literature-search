import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

function compile(file) {
  return ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
}
const dataUrl = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const layoutUrl = dataUrl(compile('../src/relationLayout.ts'));
const { buildSpaceGraph, mergeRelationSnapshots, paperSnapshot } = await import(dataUrl(compile('../src/relationGraph.ts').replace('"./relationLayout"', JSON.stringify(layoutUrl))));
const root = id => ({ id, local_id: `local-${id}`, title: `Root ${id}`, year: 2024, authors: [], color_index: id === 'A' ? 0 : 1, similarities: {} });
const paper = id => ({ id, title: `Paper ${id}`, year: 2024, strength: 80, semantic_score: .8, keyword_score: .5, citation_score: .6, reasons: [], evidence: [] });
const group = (owner, ids, category = 'semantic') => ({ center_id: owner, category, status: 'completed', matched_count: ids.length, items: ids.map(paper) });
const payload = (centers, satellites) => ({ centers, satellites, center_edges: [], overlaps: [], warnings: [], computed_at: '' });

test('promoting a child keeps one identity, its original edge and its own children', () => {
  const a = [group('A', ['B'], 'citation'), group('A', ['a1'], 'semantic'), group('A', ['a2'], 'keyword')];
  const b = [group('B', ['A'], 'citation'), group('B', ['b1'], 'semantic'), group('B', ['b2'], 'keyword')];
  const before = buildSpaceGraph(payload([root('A')], a));
  assert.equal(before.nodes.find(n => n.id === 'B').center, undefined);
  const after = buildSpaceGraph(payload([root('A'), root('B')], [...a, ...b]));
  assert.equal(after.nodes.length, 6);
  assert.equal(after.nodes.filter(n => n.center).length, 2);
  assert.equal(after.nodes.filter(n => n.id === 'B').length, 1);
  assert.equal(after.edges.filter(e => ['A', 'B'].includes(e.source) && ['A', 'B'].includes(e.target)).length, 1);
  assert.equal(after.edges.length, 5);
});
test('two parents share one child without a fabricated parent-to-parent edge', () => {
  const graph = buildSpaceGraph(payload([root('A'), root('B')], [group('A', ['S']), group('A', ['a1'], 'citation'), group('A', ['a2'], 'keyword'),
    group('B', ['S']), group('B', ['b1'], 'citation'), group('B', ['b2'], 'keyword')]));
  assert.equal(graph.nodes.length, 7);
  assert.deepEqual(graph.nodes.find(n => n.id === 'S').owners, ['A', 'B']);
  assert.equal(graph.edges.filter(e => e.target === 'S').length, 2);
  assert.equal(graph.edges.some(e => e.source === 'A' && e.target === 'B'), false);
  assert.equal(graph.edges.length, 6);
});
test('cross-source aliases promote an existing small node rather than duplicate it', () => {
  const b = { ...root('arxiv:B'), aliases: ['s2:B', 'doi:B'] };
  const candidate = { ...paper('s2:B'), aliases: ['doi:B'] };
  const graph = buildSpaceGraph(payload([root('A'), b], [{ ...group('A', []), items: [candidate] }]));
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.edges[0].target, 'arxiv:B');
  assert.ok(graph.nodes.every(n => n.center));
});
test('one node may have three relation types without tripling the paper', () => {
  const graph = buildSpaceGraph(payload([root('A')], ['citation', 'semantic', 'keyword'].map(c => group('A', ['S'], c))));
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.edges.length, 3);
  assert.deepEqual(graph.nodes.find(n => n.id === 'S').categories, ['citation', 'semantic', 'keyword']);
});
test('citation arrows require direct evidence and reciprocal reports do not become mutual citations', () => {
  const outgoing = { ...paper('B'), evidence: [{ type: 'out' }] };
  const incoming = { ...paper('A'), evidence: [{ type: 'in' }] };
  const graph = buildSpaceGraph(payload([root('A'), root('B')], [
    { ...group('A', [], 'citation'), items: [outgoing] }, { ...group('B', [], 'citation'), items: [incoming] },
    group('A', ['S'], 'semantic'),
  ]));
  assert.equal(graph.edges.length, 2);
  assert.equal(graph.edges[0].arrow, 'out');
  assert.equal(graph.edges[1].arrow, null);
});
test('all selected roots remain visible even when one has no fetched relationships', () => {
  const graph = buildSpaceGraph(payload([root('A'), root('B')], [group('A', ['S'])]));
  assert.equal(graph.nodes.filter(n => n.center).length, 2);
  for (const n of graph.nodes) assert.ok(Number.isFinite(n.x) && Number.isFinite(n.y));
});

test('only the first ranked recommendation in each dimension is displayed; no backfill', () => {
  const graph = buildSpaceGraph(payload([root('A')], ['citation', 'semantic', 'keyword'].map(c => group('A', ['S', 'lower-rank'], c))));
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.edges.length, 3);
  assert.equal(graph.nodes.some(n => n.id === 'lower-rank'), false);
});

test('B may recommend C without erasing A–B or requiring B to recommend A', () => {
  const a = payload([root('A')], [group('A', ['B'])]);
  const original = JSON.stringify(a);
  const b = payload([root('B')], [group('B', ['C']), group('B', ['D'], 'keyword'), group('B', ['E'], 'citation')]);
  const graph = buildSpaceGraph(mergeRelationSnapshots([a, b]));
  assert.equal(graph.nodes.filter(n => n.id === 'B').length, 1);
  assert.equal(graph.edges.filter(e => e.source === 'B' || e.target === 'B').length, 4);
  assert.ok(graph.edges.some(e => e.source === 'A' && e.target === 'B'));
  assert.ok(graph.edges.some(e => e.source === 'B' && e.target === 'C'));
  assert.equal(JSON.stringify(a), original, 'expanding B cannot mutate A’s original result');
});

test('pending imported paper is promoted using its known aliases before discovery finishes', () => {
  const a = payload([root('A')], [group('A', ['s2:B'])]);
  const pending = paperSnapshot({ id: 'arxiv:B', arxiv_id: 'B', semantic_scholar_id: 'B', local_id: 'local-B', title: 'B', year: 2024, authors: [] }, 'local-B');
  const graph = buildSpaceGraph(mergeRelationSnapshots([a, pending]));
  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.nodes.filter(n => n.center).length, 2);
  assert.equal(graph.edges[0].target, 'arxiv:B');
});


test('many roots retain all nodes and grow the layout beyond the five-root canvas', () => {
  const roots = Array.from({length: 30}, (_, i) => root(`R${i}`));
  const graph = buildSpaceGraph(payload(roots, roots.map(r => group(r.id, [`child-${r.id}`]))));
  assert.equal(graph.nodes.filter(n => n.center).length, 30);
  assert.equal(graph.edges.length, 30);
  assert.equal(new Set(graph.nodes.filter(n => n.center).map(n => `${n.x},${n.y}`)).size, 30);
  assert.ok(graph.width > 1900 && graph.height > 1400);
});
