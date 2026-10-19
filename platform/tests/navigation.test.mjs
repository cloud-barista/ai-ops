import assert from 'node:assert/strict';
import test from 'node:test';
const navigation=await import('../navigation.mjs');

function workspaceFixture(){
  const pages=['overview','application','infrastructure','planning','deployment','operation','validation','events'].map(id=>({
    id,dataset:{workspacePage:id},classes:new Set(id==='overview'?['current-page']:[]),
    classList:{toggle(name,enabled){if(enabled)this.owner.classes.add(name);else this.owner.classes.delete(name);}},
    closest(){return this;},
  }));
  for(const page of pages)page.classList.owner=page;
  const nested={closest:()=>pages.find(p=>p.id==='operation')};
  const comparison={closest:()=>pages.find(p=>p.id==='validation')};
  const doc={body:{dataset:{}},querySelectorAll:()=>pages,getElementById:id=>id==='inference-sample'?nested:['llm-eval-panel','planning-revisions-panel','operation-policy-comparison'].includes(id)?comparison:pages.find(p=>p.id===id)};
  return {doc,pages};
}

test('stage navigation hides unrelated workspaces when moving from selection to planning',()=>{
  const {doc,pages}=workspaceFixture();
  assert.equal(navigation.showWorkspacePage(doc,'#infrastructure'),'#infrastructure');
  assert.deepEqual(pages.filter(p=>p.classes.has('current-page')).map(p=>p.id),['infrastructure']);
  assert.equal(navigation.showWorkspacePage(doc,'#planning'),'#planning');
  assert.deepEqual(pages.filter(p=>p.classes.has('current-page')).map(p=>p.id),['planning']);
});

test('links to nested observation details reveal their owning operation workspace',()=>{
  const {doc,pages}=workspaceFixture();
  assert.equal(navigation.showWorkspacePage(doc,'#inference-sample'),'#operation');
  assert.deepEqual(pages.filter(p=>p.classes.has('current-page')).map(p=>p.id),['operation']);
  assert.equal(doc.body.dataset.workspaceView,'operation');
});

test('missing deep links recover the overview instead of leaving every workspace hidden',()=>{
  const {doc,pages}=workspaceFixture();
  navigation.showWorkspacePage(doc,'#deployment');
  assert.equal(navigation.showWorkspacePage(doc,'#missing'),'#overview');
  assert.deepEqual(pages.filter(p=>p.classes.has('current-page')).map(p=>p.id),['overview']);
});
test('comparison and revision links open only the validation workspace',()=>{
  const {doc,pages}=workspaceFixture();
  for(const id of ['llm-eval-panel','planning-revisions-panel','operation-policy-comparison']){
    assert.equal(navigation.showWorkspacePage(doc,'#'+id),'#validation');
    assert.deepEqual(pages.filter(p=>p.classes.has('current-page')).map(p=>p.id),['validation']);
  }
});
test('nested evidence above the viewport does not override the visible deployment section',()=>{
  assert.equal(typeof navigation.activeNavigationTarget,'function');
  assert.equal(navigation.activeNavigationTarget([
    {hash:'#application',top:-1300},{hash:'#infrastructure',top:-900},
    {hash:'#planning',top:-350},{hash:'#deployment',top:112},
    {hash:'#operation',top:740},{hash:'#evidence',top:-600},{hash:'#events',top:1400},
  ]),'#deployment');
});
test('navigation selects the closest entered section and starts at overview',()=>{
  assert.equal(typeof navigation.activeNavigationTarget,'function');
  assert.equal(navigation.activeNavigationTarget([{hash:'#application',top:400}]),'#overview');
  assert.equal(navigation.activeNavigationTarget([{hash:'#operation',top:90},{hash:'#deployment',top:-500}]),'#operation');
});

test('the last menu stays selected when the footer prevents its target reaching the top',()=>{
  assert.equal(navigation.activeNavigationTarget([
    {hash:'#overview',top:-3500},{hash:'#operation',top:-580},
    {hash:'#evidence',top:-1900},{hash:'#events',top:417},
  ],150,{atBottom:true,viewportHeight:900}),'#events');
});

test('deep navigation opens a collapsed target and its ancestors before scrolling and focusing',()=>{
  assert.equal(typeof navigation.revealNavigationTarget,'function');
  const log=[],parent={tagName:'DETAILS',open:false,parentElement:null};
  const focus={setAttribute:(...args)=>log.push(['attribute',...args]),focus:options=>log.push(['focus',options])};
  const target={tagName:'DETAILS',open:false,parentElement:parent,querySelector:()=>focus,scrollIntoView:options=>{assert.equal(target.open,true);assert.equal(parent.open,true);log.push(['scroll',options]);}};
  const doc={getElementById:id=>id==='events'?target:null};
  assert.equal(navigation.revealNavigationTarget(doc,'#events'),target);
  assert.equal(log[0][0],'scroll');assert.equal(log.at(-1)[0],'focus');assert.equal(log.at(-1)[1].preventScroll,true);
  assert.equal(navigation.revealNavigationTarget(doc,'#missing'),null);
});
