import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/cosmosLayout.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { PLANET_COLORS, COSMOS_BG, SATELLITE_COLOR, EDGE_COLOR, MAX_CENTERS, toggleSelection, parseSeeds, similarityDistance, planetPositions, edgeDash, satelliteOrbit, graphBounds } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('palette matches the spec values exactly', () => {
  assert.deepEqual(PLANET_COLORS, ['#4D8DFF', '#A66BFF', '#FFB454', '#39D9A0', '#FF6FAE']);
  assert.equal(COSMOS_BG, '#080D1D');
  assert.equal(SATELLITE_COLOR, '#64748B');
  assert.equal(EDGE_COLOR, '#334155');
  assert.equal(MAX_CENTERS, 5);
});

test('toggleSelection appends in order, removes on re-click, rejects a sixth pick without mutating', () => {
  let selected = [];
  for (const key of ['a', 'b', 'c', 'd', 'e']) selected = toggleSelection(selected, key);
  assert.deepEqual(selected, ['a', 'b', 'c', 'd', 'e']);
  assert.equal(toggleSelection(selected, 'f'), null);
  assert.deepEqual(selected, ['a', 'b', 'c', 'd', 'e'], 'rejected pick must not mutate');
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

test('similarityDistance maps similarity to planet spacing with a null fallback', () => {
  assert.equal(similarityDistance(1), 260);
  assert.equal(similarityDistance(0), 560);
  assert.equal(similarityDistance(null), 410);
  assert.ok(similarityDistance(0.9) < similarityDistance(0.1));
});

test('planetPositions places a highly similar pair close and returns one point per center', () => {
  const pair = planetPositions([[null, 1], [1, null]]);
  assert.equal(pair.length, 2);
  const gap = Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y);
  assert.ok(Math.abs(gap - 260) < 1, `pair gap ${gap} should be 260`);
  const far = planetPositions([[null, 0.0], [0.0, null]]);
  const farGap = Math.hypot(far[0].x - far[1].x, far[0].y - far[1].y);
  assert.ok(farGap > gap * 1.5, 'dissimilar pair must sit much farther apart');
  const five = planetPositions(Array.from({ length: 5 }, (_, i) => Array.from({ length: 5 }, (_, j) => (i === j ? null : 0.5))));
  assert.equal(five.length, 5);
  assert.ok(five.every(p => Number.isFinite(p.x) && Number.isFinite(p.y)));
});

test('edgeDash styles encode the three relation categories', () => {
  assert.deepEqual(edgeDash('citation'), []);
  assert.deepEqual(edgeDash('semantic'), [5, 4]);
  assert.deepEqual(edgeDash('keyword'), [2, 4]);
  assert.deepEqual(edgeDash('mystery'), []);
});

test('satelliteOrbit rings satellites around their planet deterministically', () => {
  const a = satelliteOrbit({ x: 100, y: 50 }, 0, 0);
  const b = satelliteOrbit({ x: 100, y: 50 }, 0, 0);
  assert.deepEqual(a, b);
  assert.ok(Math.abs(Math.hypot(a.x - 100, a.y - 50) - 195) < 1e-9, 'first ring radius 195');
  const outer = satelliteOrbit({ x: 0, y: 0 }, 0, 7);
  assert.ok(Math.abs(Math.hypot(outer.x, outer.y) - 275) < 1e-9, 'second ring radius 275');
  const other = satelliteOrbit({ x: 100, y: 50 }, 1, 0);
  assert.notDeepEqual(other, a, 'different planet index shifts the angle');
});

test('graphBounds pads every planet orbit so fitView sees the whole system', () => {
  const one = graphBounds([{ x: 0, y: 0 }]);
  assert.equal(one.minX, -225);
  assert.equal(one.maxX, 225);
  const two = graphBounds([{ x: 0, y: 0 }, { x: 260, y: 0 }]);
  assert.equal(two.minX, -225);
  assert.equal(two.maxX, 485);
  assert.equal(two.maxY - two.minY, 450);
});
