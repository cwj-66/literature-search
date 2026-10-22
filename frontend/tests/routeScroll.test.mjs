import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source=readFileSync(new URL('../src/routeScroll.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exports={};
new Function('require','exports',js)(createRequire(import.meta.url),exports);
const {scrollPageKey}=exports;

test('choosing a lower field retains the same scrollable page',()=>{
  assert.equal(scrollPageKey('/w/rag/fields','?field=first'),scrollPageKey('/w/rag/fields','?field=third'));
  assert.notEqual(scrollPageKey('/w/rag/fields',''),scrollPageKey('/w/other/fields',''));
});
test('paper details and separate list pages retain separate return positions',()=>{
  assert.notEqual(scrollPageKey('/w/rag/library',''),scrollPageKey('/w/rag/p/paper-one',''));
  assert.notEqual(scrollPageKey('/library','?page=1'),scrollPageKey('/library','?page=2'));
});
test('graph selection never resets the graph scroll position',()=>{
  assert.equal(scrollPageKey('/w/rag/graph','?focus=a'),scrollPageKey('/w/rag/graph','?focus=b'));
  assert.equal(scrollPageKey('/graph','?focus=a'),scrollPageKey('/graph','?focus=b'));
});
