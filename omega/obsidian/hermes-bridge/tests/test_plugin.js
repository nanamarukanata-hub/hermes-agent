'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname,'..','main.js'),'utf8');
const exportsModule = {exports:{}};
const sample = {
  Plugin:class{}, ItemView:class{}, PluginSettingTab:class{}, Setting:class{},
  Notice:class{}, requestUrl:async()=>{throw Error('mock network must not be used');},
};
const context = vm.createContext({module:exportsModule,require:(n)=>n==='obsidian'?sample:require(n),URL,console,document:{}});
vm.runInContext(code,context,{filename:'main.js'});
const api = exportsModule.exports._test;
assert.equal(api.validatedEndpoint('http://127.0.0.1:8642/v1/'),'http://127.0.0.1:8642/v1');
assert.equal(api.validatedEndpoint('http://localhost:8642/v1'),'http://localhost:8642/v1');
for(const url of ['https://evil.test/v1','http://127.0.0.1:8642/admin','http://user:pw@localhost:8642/v1','http://example.org/v1','file:///tmp/x','http://127.0.0.1:8642/v1?x=1']) {
  assert.throws(()=>api.validatedEndpoint(url),/./);
}
assert.equal(api.buildUserMessage('hello','',''),'hello');
const user = api.buildUserMessage('summarize','Research/Test.md','note content');
assert.ok(user.includes('<reference-note path="Research/Test.md">'));
assert.ok(user.includes('note content'));
assert.ok(!user.includes('undefined'));
assert.equal(api.textFromResponse({choices:[{message:{content:'Confirmed response'}}]}),'Confirmed response');
assert.throws(()=>api.textFromResponse({choices:[]}),/./);
assert.equal(JSON.parse(fs.readFileSync(path.join(__dirname,'..','manifest.json'),'utf8')).isDesktopOnly,true);
assert.ok(code.includes('this.keyInput.value.trim()'));
assert.ok(code.includes('await this.plugin.saveData({endpoint:checked})'));
assert.ok(!code.includes('saveData({key:'));
console.log('Hermes Obsidian bridge: 14 checks passed.');
