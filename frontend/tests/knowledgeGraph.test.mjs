import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/knowledgeGraph.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { filterGraph, canvasGraph, topicStats, relatedPapers, topicGroups } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const graph = {
  total_papers: 3, scope: 'test',
  nodes: [
    { id: 'p1', kind: 'paper', label: 'First', year: 2022 }, { id: 'p2', kind: 'paper', label: 'Second', year: 2024 },
    { id: 'p3', kind: 'paper', label: 'Unknown', year: null },
    { id: 'k1', kind: 'keyword', label: 'RAG' }, { id: 'k2', kind: 'keyword', label: 'Retrieval' },
    { id: 'a', kind: 'author', label: 'Alice' },
  ],
  edges: [
    ...['p1', 'p2'].flatMap(source => ['k1', 'k2'].map(target => ({ source, target, relation: 'has_keyword' }))),
    { source: 'p1', target: 'a', relation: 'authored_by' }, { source: 'p3', target: 'a', relation: 'authored_by' },
    { source: 'p2', target: 'p1', relation: 'cites' },
  ],
};

test('search finds author/topic papers; bounded years exclude unknown and preserve citation endpoints', () => {
  assert.equal(filterGraph(graph, 'alice').total_papers, 2);
  assert.equal(filterGraph(graph, 'rag').total_papers, 2);
  const filtered = filterGraph(graph, '', 2024, 2024);
  assert.equal(filtered.total_papers, 1);
  assert.equal(filtered.edges.some(e => e.relation === 'cites'), false);
  assert.equal(filterGraph(graph, 'not present').nodes.length, 0);
});

test('related papers explain shared topics/authors and retain true citation direction', () => {
  const related = relatedPapers(graph, graph.nodes[0]);
  assert.equal(related.length, 2);
  assert.ok(related.find(r => r.node.id === 'p2').reasons.includes('此论文引用了当前论文'));
  assert.ok(related.find(r => r.node.id === 'p3').reasons.includes('共同作者：Alice'));
});

test('statistics dedupe per paper; first year uses publication year; clustering needs repeated co-occurrence', () => {
  const duplicate = { ...graph, edges: [...graph.edges, graph.edges[0]] };
  assert.equal(topicStats(duplicate)[0].count, 2);
  assert.equal(topicStats(duplicate)[0].firstYear, 2022);
  assert.equal(topicGroups(duplicate).length, 1);
  assert.equal(topicGroups(filterGraph(graph, '', 2022, 2022)).length, 0);
});

test('canvas cap cannot truncate statistics and keeps selected paper plus valid edges', () => {
  const large = { ...graph, nodes: [...graph.nodes, ...Array.from({ length: 50 }, (_, i) => ({ id: `extra-${i}`, kind: 'paper', label: `Extra ${i}` }))] };
  const canvas = canvasGraph(large, ['has_keyword'], 'extra-49');
  assert.equal(canvas.nodes.filter(n => n.kind === 'paper').length, 36);
  assert.ok(canvas.nodes.some(n => n.id === 'extra-49'));
  assert.equal(large.nodes.filter(n => n.kind === 'paper').length, 53);
  const ids = new Set(canvas.nodes.map(n => n.id));
  assert.ok(canvas.edges.every(e => ids.has(e.source) && ids.has(e.target) && e.relation === 'has_keyword'));
  assert.equal(canvasGraph(graph, [], '').edges.length, 0);
});
