import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
const { outputText } = ts.transpileModule(readFileSync(new URL('../src/scholarGraphLayout.ts',import.meta.url),'utf8'), {compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}});
const source = outputText.replace('"d3-force"', JSON.stringify(import.meta.resolve('d3-force')));
const { scholarLayout } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('layout keeps shared entities unique, all real nodes, and never mutates the supplied graph',()=>{
  const graph={nodes:[...Array.from({length:12},(_,i)=>({id:`p${i}`,kind:'paper',label:`Paper ${i}`})),{id:'shared',kind:'keyword',label:'Shared keyword'}],edges:[{source:'p0',target:'shared',relation:'has_keyword'},{source:'p1',target:'shared',relation:'has_keyword'}],scope:'test',total_papers:12};
  const before=JSON.stringify(graph), a=scholarLayout(graph,'p0'), b=scholarLayout(graph,'p0');
  assert.equal(a.nodes.length,13);assert.equal(a.nodes.filter(n=>n.node.id==='shared').length,1);
  assert.deepEqual(a,b);assert.equal(JSON.stringify(graph),before);
  for(const n of a.nodes){assert.ok(Number.isFinite(n.x)&&Number.isFinite(n.y));assert.ok(n.x>=n.width/2&&n.x<=a.width-n.width/2);assert.ok(n.y>0&&n.y<a.height);}
});
test('empty and single-paper graphs have valid finite canvas geometry',()=>{
  for(const nodes of [[],[{id:'one',kind:'paper',label:'One'}]]){
    const g=scholarLayout({nodes,edges:[],scope:'test',total_papers:nodes.length},'');
    assert.ok(g.width>0&&g.height>0);assert.equal(g.nodes.length,nodes.length);
  }
});
