import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

function transpile(file) {
  const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
  return ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
}

const themeJs = transpile('theme.ts');
const chartsJs = transpile('themeCharts.ts').replace(/from "\.\/theme"/, `from "data:text/javascript;base64,${Buffer.from(themeJs).toString('base64')}"`);
const { median, themeTotal, themeYearCount, quadrantOption, themeRiverOption, sankeyOption, QUADRANT_META } = await import(`data:text/javascript;base64,${Buffer.from(chartsJs).toString('base64')}`);

const themes = [
  { id: 'theme:rag', name: '检索增强生成', description: 'RAG 系统', keywords: ['rag', 'llm'], paper_ids: ['paper:x'], size: 3, centrality: 0.8, density: 0.8, quadrant: 'motor', by_year: { '2023': 1, '2024': 20 } },
  { id: 'theme:ir', name: '信息检索', description: '', keywords: ['retrieval'], paper_ids: [], size: 2, centrality: 0.2, density: 0.9, quadrant: 'basic', by_year: { '2022': 2 } },
  { id: 'theme:agent', name: '智能体', description: '', keywords: ['agent'], paper_ids: [], size: 1, centrality: 0.9, density: 0.1, quadrant: 'emerging', by_year: { '2024': 1 } },
  { id: 'theme:x', name: '小众主题', description: '', keywords: [], paper_ids: [], size: 1, centrality: 0.1, density: 0.1, quadrant: 'niche', by_year: { '2024': 1 } },
];

test('median splits sorted values for odd and even counts', () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 3, 2]), 2.5);
  assert.equal(median([]), 0);
});

test('theme totals and per-year lookups read by_year defensively', () => {
  assert.equal(themeTotal(themes[0]), 21);
  assert.equal(themeYearCount(themes[0], 2023), 1);
  assert.equal(themeYearCount(themes[0], 2020), 0);
});

test('quadrant option draws median cross, colors by quadrant and scales point size by paper count', () => {
  const option = quadrantOption(themes);
  const series = option.series[0];
  const markLines = series.markLine.data.map(item => Object.keys(item)[0]).sort();
  assert.deepEqual(markLines, ['xAxis', 'yAxis']);
  assert.equal(series.markLine.data.find(item => item.xAxis).xAxis, 0.5); // median of 0.1/0.2/0.8/0.9
  assert.equal(series.markLine.data.find(item => item.yAxis).yAxis, 0.45); // median of 0.1/0.1/0.8/0.9
  assert.equal(series.markArea.data.length, 4);
  const byName = Object.fromEntries(series.data.map(item => [item.name, item]));
  assert.equal(byName['检索增强生成'].itemStyle.color, QUADRANT_META.motor.color);
  assert.equal(byName['信息检索'].itemStyle.color, QUADRANT_META.basic.color);
  assert.equal(byName['智能体'].itemStyle.color, QUADRANT_META.emerging.color);
  assert.equal(byName['小众主题'].itemStyle.color, QUADRANT_META.niche.color);
  assert.ok(byName['检索增强生成'].symbolSize > byName['智能体'].symbolSize);
  assert.deepEqual(byName['检索增强生成'].value, [0.8, 0.8]);
});

test('theme river emits one row per kept theme and year, ranked by total and capped', () => {
  const many = Array.from({ length: 15 }, (_, i) => ({ ...themes[3], id: `t${i}`, name: `主题${i}`, by_year: { '2024': i + 1 } }));
  const option = themeRiverOption([...themes, ...many], [2022, 2023, 2024]);
  const rows = option.series[0].data;
  assert.equal(rows.length, 12 * 3); // RIVER_MAX_THEMES = 12
  assert.ok(rows.every(row => row[0] === String(2022) || row[0] === String(2023) || row[0] === String(2024)));
  assert.ok(rows.every(row => typeof row[1] === 'number' && row[1] >= 0));
  const totals = Object.fromEntries([...themes, ...many].map(t => [t.name, themeTotal(t)]));
  const kept = new Set(rows.map(row => row[2]));
  assert.ok(kept.has('检索增强生成'));
  for (const name of kept) assert.ok(totals[name] >= 4, `kept theme ${name} should not be smaller than dropped ones`);
  const small = themeRiverOption(themes.slice(0, 2), [2024]);
  assert.deepEqual(small.series[0].data, [['2024', 20, '检索增强生成'], ['2024', 0, '信息检索']]);
});

test('sankey builds per-period nodes with labels, depths and weighted links', () => {
  const links = [
    { from: 't1', to: 't2', weight: 0.7, from_period: 0, to_period: 1, from_label: '信息检索', to_label: 'RAG' },
    { from: 't1', to: 't3', weight: 0.3, from_period: 0, to_period: 1, from_label: '信息检索', to_label: '智能体' },
    { from: 't3', to: 't4', weight: 0, from_period: 1, to_period: 2, from_label: '智能体', to_label: '智能体' },
  ];
  const option = sankeyOption(links);
  const series = option.series[0];
  assert.deepEqual(series.data.map(n => n.name).sort(), ['0|信息检索', '1|RAG', '1|智能体', '2|智能体']);
  assert.equal(series.data.find(n => n.name === '2|智能体').depth, 2);
  assert.equal(series.links.length, 3);
  const first = series.links.find(l => l.source === '0|信息检索' && l.target === '1|RAG');
  assert.equal(first.value, 0.7);
  const zero = series.links.find(l => l.target === '2|智能体');
  assert.equal(zero.value, 0.01); // clamped so the edge still renders
});
