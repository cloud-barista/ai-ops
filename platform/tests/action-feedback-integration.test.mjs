import test from 'node:test';
import assert from 'node:assert/strict';
import {setupPrometheusMonitoring} from '../prometheus-ui.mjs';
import {setupValidationUI} from '../validation-ui.mjs';
import {setupCloudUI} from '../cloud-ui.mjs';
import {setupLlmComparison} from '../llm-comparison-ui.mjs';
import {uiDocument} from './fixtures/ui-document.mjs';

function documentFixture(){
  const document=uiDocument(),get=document.getElementById;
  document.getElementById=id=>{
    let element=get(id);
    if(!element){element=document.createElement('div');element.dataset={};document.elements[id]=element;Object.defineProperty(element,'options',{get:()=>element.children});}
    return element;
  };
  document.body={dataset:{workspaceView:'operation'}};return document;
}
function recorder(){
  const events=[];
  return {events,async runAction(label,work){events.push({label,state:'pending'});try{const result=await work();events.push({label,state:'success'});return result;}catch(error){events.push({label,state:'error'});throw error;}}};
}
function globalFixture(t,name,value){
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,name);Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
  t.after(()=>{if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];});
}

test('manual monitoring requests announce work and failure, while automatic collection stays silent',async t=>{
  t.mock.method(globalThis,'setInterval',()=>1);
  const document=documentFixture(),feedback=recorder();let fail=false;
  const receipt={flow_id:'flow-monitor',input:{execution_target:'local'},execution:{status:'RUNNING',execution_id:'app-monitor'}};
  const monitor=setupPrometheusMonitoring({document,getReceipt:()=>receipt,isBusy:()=>false,onFeedback:async()=>{},runAction:feedback.runAction,api:async()=>{if(fail)throw Error('offline');return {flow_id:receipt.flow_id,state:'WAITING_REQUESTS',server_ready:true};}});
  await monitor.refresh();assert.equal(feedback.events.length,0);
  await document.elements['prometheus-refresh'].dispatch('click');
  assert.deepEqual(feedback.events.map(e=>e.state),['pending','success']);
  fail=true;await document.elements['prometheus-refresh'].dispatch('click');
  assert.equal(feedback.events.at(-1).state,'error');assert.equal(document.elements['prometheus-refresh'].disabled,false);
  assert.match(document.elements['prometheus-status'].textContent,/연결 대기/);
});

test('manual saved-result failure is not swallowed before the loading controller sees it',async()=>{
  const document=documentFixture(),feedback=recorder();let fail=false;
  const controller=setupValidationUI({document,runAction:feedback.runAction,api:async path=>{if(path==='/cloud/readiness')return {enabled:false,pending_cleanup:[]};if(fail)throw Error('unavailable');return {results:[]};}});
  await controller.ready;assert.equal(feedback.events.length,0);
  fail=true;await document.elements['validation-reload'].dispatch('click');
  assert.deepEqual(feedback.events.map(e=>e.state),['pending','error']);
  assert.match(document.elements['validation-status'].textContent,/실패/);assert.equal(document.elements['validation-reload'].disabled,false);
});

test('cloud request feedback preserves the gate that prevents approval when actual AWS execution is disabled',async t=>{
  t.mock.method(globalThis,'setInterval',()=>1);
  const document=documentFixture();globalFixture(t,'document',document);
  const feedback=recorder();let resolveRequest;
  const request=new Promise(resolve=>{resolveRequest=resolve;});
  let receipt={flow_id:'flow-cloud',selection_version:1,input:{execution_target:'cloud'},geon:{guard:{status:'APPROVED'}}};
  document.getElementById('cloud-lifetime').value='1800';
  const controller=setupCloudUI({api:async path=>path==='/cloud/readiness'?{enabled:false,checks:{}}:request,getReceipt:()=>receipt,onReceipt:r=>{receipt=r;},runAction:feedback.runAction});
  await Promise.resolve();controller.render();
  const pending=document.elements['cloud-review'].dispatch('click');assert.equal(feedback.events[0].state,'pending');
  resolveRequest({...receipt,cloud:{state:'REVIEWED'}});await pending;
  assert.equal(feedback.events.at(-1).state,'success');assert.equal(document.elements['cloud-approve'].disabled,true);
  controller.dispose();
});

test('an accepted cloud deployment keeps polling even while its initial receipt still says READY',async t=>{
  let tick;t.mock.method(globalThis,'setInterval',callback=>{tick=callback;return 1;});t.mock.method(globalThis,'clearInterval',()=>{});
  const document=documentFixture();globalFixture(t,'document',document);const calls=[];
  let receipt={flow_id:'flow-cloud-pending',cloud_run_id:'run-pending',cloud_job_requested:true,input:{execution_target:'cloud'},cloud:{state:'READY'},geon:{guard:{status:'APPROVED'}}};
  const controller=setupCloudUI({getReceipt:()=>receipt,onReceipt:r=>{receipt=r;},api:async(path,body)=>{calls.push({path,body});if(path==='/cloud/readiness')return {enabled:true};return {...receipt,cloud:{state:'RUNNING'}};}});
  await Promise.resolve();controller.render();await tick();
  assert.ok(calls.some(c=>c.path==='/flows/flow-cloud-pending/cloud/status'&&c.body===undefined));
  controller.render();assert.match(document.elements['cloud-state'].textContent,/실행 중/);controller.dispose();
});
test('an independent new analysis still shows a link to its existing owned AWS run without launching anything',async t=>{
 t.mock.method(globalThis,'setInterval',()=>1);
 const document=documentFixture();globalFixture(t,'document',document);const calls=[];
 const controller=setupCloudUI({getReceipt:()=>null,onReceipt:()=>{throw Error('must not replace new analysis');},api:async(path,body)=>{calls.push({path,body});return {enabled:true,pending_cleanup:[{flow_id:'flow-existing',state:'RUNNING',instance_id:'i-existing'}]};}});
 await Promise.resolve();controller.render();
 assert.equal(document.elements['active-cloud-notice'].hidden,false);
 const link=document.elements['active-cloud-links'].children[0];assert.equal(link.href,'/?flow=flow-existing#deployment');
 assert.ok(calls.every(c=>c.path==='/cloud/readiness'&&c.body===undefined));controller.dispose();
});

function llmFixture(t,{failStart=false}={}){
  const document=documentFixture(),activities=[];globalFixture(t,'document',document);
  globalFixture(t,'localStorage',{getItem:()=>null,setItem:()=>{}});
  let scheduled,state='RUNNING';t.mock.method(globalThis,'setTimeout',callback=>{scheduled=callback;return 1;});t.mock.method(globalThis,'clearTimeout',()=>{});
  const record=()=>({run_id:'llm-test',state,results:[],summary:{candidates:[]},completed_count:state==='RUNNING'?2:6,total_count:6,suite_sha256:'fixed-hash',candidates:[]});
  const controller=setupLlmComparison({isBusy:()=>false,onSelectionChange:()=>{},onActivityChange:s=>activities.push(s),api:async(path,data)=>{
    if(path==='/llm-candidates')return {candidates:[]};if(path==='/llm-comparisons'&&!data)return {runs:[]};
    if(data&&failStart)throw Error('start_failed');return record();
  }});
  return {controller,document,activities,complete:async()=>{state='COMPLETED';await scheduled();}};
}

test('LLM comparison feedback follows the asynchronous job until completion instead of completing after acceptance',async t=>{
  const f=llmFixture(t);await f.controller.refresh();assert.ok(f.activities.every(s=>s.silent));
  await f.document.elements['llm-eval-run'].dispatch('click');
  assert.equal(f.activities.at(-1).busy,true);assert.match(f.activities.at(-1).detail,/2 \/ 6/);
  await f.complete();assert.equal(f.activities.at(-1).busy,false);assert.equal(f.activities.at(-1).failed,false);
});

test('LLM start failure ends the progress indicator with an error',async t=>{
  const f=llmFixture(t,{failStart:true});await f.controller.refresh();
  await f.document.elements['llm-eval-run'].dispatch('click');
  assert.equal(f.activities.at(-1).busy,false);assert.equal(f.activities.at(-1).failed,true);
  assert.equal(f.document.elements['llm-eval-reload'].disabled,false);
});
test('a locked live experiment rejects model selection without disabling read-only comparison history',async t=>{
 const document=documentFixture();globalFixture(t,'document',document);globalFixture(t,'localStorage',{getItem:()=>null,setItem:()=>{}});let changed=0;
 const controller=setupLlmComparison({isBusy:()=>false,canChangeSelection:()=>false,onSelectionChange:()=>changed++,onActivityChange:()=>{},api:async path=>path==='/llm-candidates'?{candidates:[]}:{runs:[]}});
 await controller.refresh();const before=document.elements['profile-llm'].value;
 controller.choose('llama3.2-profile-generator');assert.equal(changed,0);assert.equal(document.elements['profile-llm'].value,before);
 assert.equal(document.elements['llm-eval-reload'].disabled,false);
});
