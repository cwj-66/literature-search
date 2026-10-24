import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
async function load(name) {
 const src=readFileSync(new URL('../src/'+name+'.ts',import.meta.url),'utf8');
 const js=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}}).outputText;
 return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
}
const {reconcileChats}=await load('chatSync');
const {transformGesture}=await load('canvasGestures');
const chat={id:'saved',title:'Research',turns:[{id:1,question:'q',task:{task_id:'real-task',answer:'a'}}],selectedIds:[],version:4};
test('stale no-op dirty snapshot adopts server version and clears conflict',()=>{
 const result=reconcileChats([{...chat,version:2,dirty:true}], [{...chat,turns:[{...chat.turns[0],demo_example:true}]}]);
 assert.equal(result.pending.length,0);assert.equal(result.chats[0].version,4);assert.equal(result.chats[0].dirty,false);
});
test('concurrent local edits survive separately without overwriting newer remote content',()=>{
 const local={...chat,version:2,dirty:true,turns:[...chat.turns,{id:2,question:'unsaved question'}]};
 const result=reconcileChats([local],[chat],()=> 'recovered');
 assert.equal(result.chats[0].turns.length,1);assert.equal(result.chats[1].id,'recovered');
 assert.equal(result.pending[0].version,0);assert.equal(result.pending[0].turns.at(-1).question,'unsaved question');
});
test('same-version edits keep identity; clean deleted snapshots stay deleted',()=>{
 assert.equal(reconcileChats([{...chat,dirty:true,title:'edit'}],[chat]).pending[0].id,'saved');
 assert.deepEqual(reconcileChats([chat],[]).chats,[]);
});
test('pinch doubles graph scale while preserving the graph point under the midpoint',()=>{
 const next=transformGesture({x:10,y:20,k:1},[{x:50,y:100},{x:150,y:100}],[{x:0,y:120},{x:200,y:120}],.1,2.5);
 assert.deepEqual(next,{k:2,x:-80,y:-40});
 assert.equal((100-next.x)/next.k,90);assert.equal((120-next.y)/next.k,80);
});
test('pinch clamps scale and a remaining finger pans without resetting zoom',()=>{
 const capped=transformGesture({x:0,y:0,k:2},[{x:0,y:0},{x:10,y:0}],[{x:0,y:0},{x:100,y:0}],.1,2.5);
 assert.equal(capped.k,2.5);
 assert.deepEqual(transformGesture(capped,[{x:100,y:0}],[{x:110,y:15}],.1,2.5),{...capped,x:capped.x+10,y:capped.y+15});
});
