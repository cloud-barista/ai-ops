import assert from 'node:assert/strict';
import test from 'node:test';
import {setupAgents} from '../agents-ui.mjs';
import {uiDocument} from './fixtures/ui-document.mjs';

const ids=['decision-agent','operation-agent','agents-status','agents-refresh','agents-save','operation-agents-save','decision-agent-status','operation-agent-status','decision-agent-execution','operation-agent-execution','agent-register','agent-register-button','agent-name','agent-version'];
const registry={agents:[{},{}],eligible_decision_agents:[{name:'DecisionA'},{name:'DecisionB'}],eligible_operation_agents:[{name:'OperationA'},{name:'OperationB'}],defaults:{ai_application_automation:'DecisionA',ai_application_operation_optimization:'OperationA'}};

async function fixture(t,receipt={flow_id:'flow-test',selection_version:1,agents:{decision:{name:'DecisionA'},operation:{name:'OperationA'}}}){
  const previous=globalThis.document,document=uiDocument(ids),calls=[];
  globalThis.document=document;t.after(()=>{globalThis.document=previous;});
  const api=async(path,value)=>{if(path==='/agents')return registry;calls.push({path,value});receipt={...receipt,agents:{decision:{name:value.decision_agent},operation:{name:value.operation_agent}}};return receipt;};
  const controller=setupAgents({api,getReceipt:()=>receipt,isBusy:()=>false,onReceipt:next=>{receipt=next;}});
  await controller.refresh();
  return {document,calls,controller};
}

test('saving the operation role leaves an unsaved decision choice untouched in the server request',async t=>{
  const {document,calls}=await fixture(t);
  document.elements['decision-agent'].value='DecisionB';
  document.elements['operation-agent'].value='OperationB';
  await document.elements['operation-agents-save'].dispatch('click');
  assert.equal(calls.length,1);
  assert.equal(calls[0].value.decision_agent,'DecisionA');
  assert.equal(calls[0].value.operation_agent,'OperationB');
  assert.match(document.elements['operation-agent-status'].textContent,/적용/);
});

test('saving the decision role does not commit an operation choice from a different menu',async t=>{
  const {document,calls}=await fixture(t);
  document.elements['decision-agent'].value='DecisionB';
  document.elements['operation-agent'].value='OperationB';
  await document.elements['agents-save'].dispatch('click');
  assert.equal(calls[0].value.decision_agent,'DecisionB');
  assert.equal(calls[0].value.operation_agent,'OperationA');
});

test('a running flow freezes deployment-role changes but can save the current operation role',async t=>{
  const {document,controller}=await fixture(t,{flow_id:'flow-test',selection_version:1,execution:{status:'RUNNING'},agents:{decision:{name:'DecisionA'},operation:{name:'OperationA'}}});
  controller.render();
  assert.equal(document.elements['agents-save'].disabled,true);
  assert.equal(document.elements['operation-agents-save'].disabled,false);
});
test('operation selection preserves the actual deployment agent in older execution records',async t=>{
  const {document,calls}=await fixture(t,{flow_id:'flow-test',selection_version:1,execution:{status:'RUNNING'},geon:{agent_execution:{agent_name:'DecisionB'}}});
  assert.equal(document.elements['decision-agent'].value,'DecisionB');
  document.elements['operation-agent'].value='OperationB';
  await document.elements['operation-agents-save'].dispatch('click');
  assert.equal(calls[0].value.decision_agent,'DecisionB');
  assert.equal(calls[0].value.operation_agent,'OperationB');
});

test('cloud preparation disables both role edits until a bound group is running',async t=>{
 const {document,controller}=await fixture(t,{flow_id:'flow-test',cloud_job_requested:true,cloud:{state:'PREPARING'}});controller.render();
 assert.equal(document.elements['agents-save'].disabled,true);assert.equal(document.elements['operation-agents-save'].disabled,true);
});

test('a bound running cloud group permits only its next operation role',async t=>{
 const {document,controller}=await fixture(t,{flow_id:'flow-test',cloud_job_requested:true,cloud:{state:'RUNNING',app:{group_id:'group-test'}},execution:{status:'RUNNING'},agents:{decision:{name:'DecisionA'},operation:{name:'OperationA'}},appdeploy:{group_id:'group-test'},replica_group:{group_id:'group-test',operation_state:'IDLE'}});controller.render();
 assert.equal(document.elements['agents-save'].disabled,true);assert.equal(document.elements['operation-agents-save'].disabled,false);
});
