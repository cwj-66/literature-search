import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source=readFileSync(new URL('../src/presentation.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {pageTitle,formatMessageTime,evidenceLabels,displayProse}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
test('all page titles use one product name without repeating it',()=>{
 assert.equal(pageTitle('智能问答'),'智能问答 · 文献检索');
 assert.equal(pageTitle('文献库 - 文献检索'),'文献库 - 文献检索');
});
test('both chat interfaces use the same Chinese date and 24-hour clock',()=>{
 assert.equal(formatMessageTime('2026-10-21T16:32:00Z'),'2026/10/22 00:32');
 assert.equal(formatMessageTime(Date.parse('2026-10-21T16:32:00Z')),'2026/10/22 00:32');
 assert.equal(formatMessageTime('invalid'),'时间未知');
});
test('nonconsecutive internal references have plain sequential source labels',()=>{
 const labels=evidenceLabels([{evidence_id:'E0001'},{evidence_id:'E0017'}]);
 assert.equal(labels.get('E0001'),'来源 1');assert.equal(labels.get('E0017'),'来源 2');
});
test('saved editorial prose has readable API spacing and no mixed jargon',()=>{
 assert.equal(displayProse('公开API文献样本'),'公开 API 文献样本');
 assert.equal(displayProse('概念 grounded 上下文集合'),'有原文依据的概念上下文集合');
});
