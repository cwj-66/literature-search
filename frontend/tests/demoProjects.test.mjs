import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';

const source=readFileSync(new URL('../src/demoProjects.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
const {refreshDemoProjects}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const paper=(id,extra={})=>({paper:{id,title:id},status:'unread',note:'',addedAt:'old',...extra});
test('refreshes membership/order while preserving reader notes and own workspaces',()=>{
 const own={id:'own',name:'own',papers:{own:paper('own')}};
 const cached={currentId:'sample',projects:[{id:'sample',name:'old',papers:{a:paper('A',{status:'read',note:'my note',openedAt:'yesterday'}),b:paper('B')}},own]};
 const remote=[{id:'sample',name:'new default',papers:{a:paper('A',{addedAt:'new'}),c:paper('C')}}];
 const result=refreshDemoProjects(cached,remote);
 assert.equal(result.currentId,'sample');assert.equal(result.projects[0].name,'new default');
 assert.deepEqual(Object.keys(result.projects[0].papers),['a','c']);
 assert.equal(result.projects[0].papers.a.status,'read');assert.equal(result.projects[0].papers.a.note,'my note');
 assert.equal(result.projects[0].papers.a.addedAt,'new');assert.equal(result.projects[1],own);
 assert.equal(cached.projects[0].papers.a.addedAt,'old');
});
test('does not resurrect a locally deleted workspace',()=>{
 assert.deepEqual(refreshDemoProjects({currentId:'',projects:[]},[{id:'sample',papers:{}}]).projects,[]);
});
