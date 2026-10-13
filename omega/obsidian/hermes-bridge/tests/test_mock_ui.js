'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Node {
  constructor() {this.children=[];this.style={};this.value='';this.checked=false;this.disabled=false;this.scrollHeight=50;this.events={};}
  empty() {this.children=[];this.textContent='';}
  addClass(cls){this.cls=cls;return this;}
  setText(t){this.textContent=t;}
  createEl(tag,opts={}) {const x=new Node();x.tag=tag;x.textContent=opts.text||'';this.children.push(x);return x;}
  createDiv(opts={}){return this.createEl('div',opts);}
  appendChild(x){this.children.push(x);return x;}
  addEventListener(evt,fn){this.events[evt]=fn;}
}
const known = new Map([['Test.md',{path:'Test.md',extension:'md'}]]);
let writes=[], requestCount=0, sent;
const app={
  vault:{
    getAbstractFileByPath:p=>known.get(p)||null,
    read:async()=> '# Test title\nMIX TECHNIQUES',
    createFolder:async(p)=>{known.set(p,{path:p,extension:''});},
    create:async(p,c)=>{writes.push({path:p,content:c});known.set(p,{path:p,extension:'md'});},
  },
  workspace:{
    on:()=>({off(){}}), getActiveFile:()=>known.get('Test.md'), getLeavesOfType:()=>[],
    getRightLeaf:()=>({setViewState:async()=>{}}), revealLeaf:async()=>{},detachLeavesOfType:()=>{},
  },
};
let registered,stored=[];
const mock = {
  Plugin:class {constructor(){this.app=app;}loadData(){return Promise.resolve({})}saveData(x){stored.push(x);return Promise.resolve()}registerEvent(){}registerView(k,fn){registered=fn;}addRibbonIcon(){}addCommand(){}addSettingTab(){}},
  ItemView:class {constructor(){this.app=app;this.containerEl={children:[new Node(),new Node()]};}},
  PluginSettingTab:class{},Setting:class{},Notice:class{},
  requestUrl:async(o)=>{requestCount++;sent=o;return {status:200,json:{choices:[{message:{content:'Hermes replied correctly'}}]}};},
};
const mod={exports:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','main.js'),'utf8'),{module:mod,require:n=>n==='obsidian'?mock:require(n),document:{createElement:()=>new Node()},URL,console,Date,Error});
(async()=>{
  const PluginClass=mod.exports;
  const plugin=new PluginClass();await plugin.onload();
  const panel=registered({});await panel.onOpen();
  panel.prompt.value='Summarize current note';panel.keyInput.value='local-secret';panel.contextCheckbox.checked=true;
  await panel.send();
  assert.equal(requestCount,1);
  assert.equal(sent.url,'http://127.0.0.1:8642/v1/chat/completions');
  assert.equal(sent.headers.Authorization,'Bearer local-secret');
  const message=JSON.parse(sent.body).messages.at(-1).content;
  assert.ok(message.includes('MIX TECHNIQUES'));
  assert.equal(panel.lastAnswer,'Hermes replied correctly');
  assert.equal(writes.length,0,'No automatic writes allowed');
  await panel.saveReply();
  assert.equal(writes.length,1,'Explicit click saved only one reply');
  assert.match(writes[0].path,/^01_Inbox\/Hermes\/Hermes-Reply-/);
  assert.match(writes[0].content,/status: unverified/);
  assert.ok(!JSON.stringify(stored).includes('local-secret'));
  panel.prompt.value='test';plugin.settings.endpoint='https://not-local.example/v1';
  await panel.send();
  assert.equal(requestCount,1,'External endpoints must be blocked');
  await panel.onClose();
  assert.equal(panel.keyInput.value,'');
  console.log('Hermes UI mock roundtrip: 11 checks passed.');
})().catch(e=>{console.error(e);process.exit(1)});
