import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:http';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {createClients} from '../server/upstream.mjs';
import {toExternalFlow} from '../server/geon-adapter.mjs';
import {input,profile,recommendation} from './fixtures.mjs';
const agents=await import('../server/agents.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const decision={name:'ExternalDecision',version:'1',role:'decision',enabled:true,source:'runtime',endpoint:'http://127.0.0.1:18185',invocation_path:'/execute',capabilities:['ai_application_automation'],bounded_actions:['generate_deployment_decision']};
const operation={...decision,name:'ExternalOperation',capabilities:['ai_application_operation_optimization'],bounded_actions:['generate_scaling_decision']};
function registry(){return {agents:[decision,operation],eligible_decision_agents:[decision],eligible_operation_agents:[operation],defaults:{ai_application_automation:decision.name,ai_application_operation_optimization:operation.name}};}
function required(){assert.equal(typeof agents.createAgentRoutes,'function','Agent selection routes are not connected');return agents;}
async function setup(t,extra={}){const api=required();const store=createWorkflowStore(await mkdtemp(join(tmpdir(),'khu-agents-')));await store.write({flow_id:'flow-test',selection_version:1,state:'SELECTED',history:[],...extra});return {store,clients:{agents:async()=>registry()},route:api.createAgentRoutes()};}
function select(h,value){return h.route({request:{method:'POST'},path:'/api/v1/flows/flow-test/agents',value:{selection_version:1,decision_agent:decision.name,operation_agent:operation.name,...value},store:h.store,clients:h.clients});}
test('registered_agent_is_actually_dispatched',async t=>{const h=await setup(t);const selected=(await select(h)).data;const r={...selected,input:input(),trace_id:'trace-test',selected_candidate_id:'demo-cpu8',candidates:{all:[{candidate_id:'demo-cpu8',selectable:true,spec_id:'aws+us-west-2+m6a.2xlarge'}]},application_profile:profile(),resource_recommendation:recommendation('flow-test')};assert.equal(toExternalFlow({receipt:r,selectionVersion:1}).decision_agent,'ExternalDecision');});
test('operation_agent_is_query_not_common_body',async t=>{let seen;const server=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;seen={url:req.url,body:JSON.parse(body)};res.setHeader('content-type','application/json');res.end('{}');});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));const client=createClients({geonUrl:'http://127.0.0.1:'+server.address().port});await client.feedback({contract_version:'1.0'},{operationAgent:'ExternalOperation'});assert.equal(seen.url,'/api/v1/agent-control/optimization-feedback?operation_agent=ExternalOperation');assert.deepEqual(seen.body,{contract_version:'1.0'});});
test('registry_identity_change_requires_reselection',async t=>{const h=await setup(t);const saved=(await select(h)).data.agents;const changed=registry();changed.agents[0]={...decision,endpoint:'http://127.0.0.1:18186'};assert.throws(()=>required().validateAgentSelection(changed,saved),e=>e.code==='agent_selection_stale');});
test('missing_agent_never_falls_back',async t=>{const h=await setup(t);await assert.rejects(()=>select(h,{decision_agent:'UnregisteredAgent'}),e=>e.code==='agent_unavailable');assert.equal((await h.store.read('flow-test')).agents,undefined);});
test('active_flow_cannot_replace_decision_agent',async t=>{const h=await setup(t,{execution:{status:'RUNNING',mode:'local_process'}});await assert.rejects(()=>select(h),e=>e.code==='agent_change_requires_new_flow');});
test('mismatched_actual_execution_identity_is_rejected',async t=>{const h=await setup(t);const selected=(await select(h)).data.agents;assert.throws(()=>required().assertAgentExecution(selected.decision,{agent_name:'OtherAgent',status:'completed'}),e=>e.code==='agent_execution_mismatch');});
test('pending_feedback_cannot_change_operation_agent',async t=>{const h=await setup(t,{feedback_state:'UNKNOWN'});await assert.rejects(()=>select(h),e=>e.code==='feedback_unknown');});
test('registration_rejects_unapproved_network_endpoint',async t=>{const h=await setup(t);h.clients.registerAgent=async()=>{assert.fail('unapproved registration reached GEON');};await assert.rejects(()=>h.route({request:{method:'POST'},path:'/api/v1/agents',value:{...decision,endpoint:'http://169.254.169.254'},clients:h.clients,store:h.store}),e=>e.code==='agent_endpoint_not_allowed');});
test('cloud bootstrap freezes agent selection before app processes exist',async t=>{const h=await setup(t,{cloud_job_requested:true,cloud:{state:'PREPARING'}});await assert.rejects(()=>select(h),e=>e.code==='agent_change_requires_new_flow');});

test('a settled running cloud app can change only its next operation agent',async t=>{
 const h=await setup(t);await select(h);const alternate={...operation,name:'AlternateOperation'};
 h.clients.agents=async()=>({...registry(),agents:[decision,operation,alternate]});
 await h.store.update('flow-test',r=>({...r,cloud_job_requested:true,cloud:{state:'RUNNING',app:{group_id:'group-cloud'}},execution:{status:'RUNNING'},appdeploy:{group_id:'group-cloud'},replica_group:{group_id:'group-cloud',operation_state:'IDLE'},geon:{plan:'preserved'}}));
 const r=(await select(h,{operation_agent:alternate.name})).data;
 assert.equal(r.agents.operation.name,alternate.name);assert.equal(r.agents.decision.name,decision.name);assert.deepEqual(r.geon,{plan:'preserved'});
});

test('a changing or unbound cloud group cannot switch its operation agent',async t=>{
 for(const operationState of ['UNKNOWN','PENDING']){
  const h=await setup(t);await select(h);await h.store.update('flow-test',r=>({...r,cloud_job_requested:true,cloud:{state:'RUNNING',app:{group_id:'group-cloud'}},execution:{status:'RUNNING'},appdeploy:{group_id:'group-cloud'},replica_group:{group_id:'group-cloud',operation_state:operationState}}));
  await assert.rejects(()=>select(h),e=>e.code==='agent_change_requires_new_flow');
 }
});
