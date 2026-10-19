import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createPlatformServer,digest} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {input,profile,recommendation,catalog,snapshots} from './fixtures.mjs';

function localRecommendation(id){const rec=recommendation(id);Object.assign(rec.recommendations[0].resource,{resource_id:'khu-local-pc',provider:'local'});return rec;}
async function fixture(t,override={}){
 const store=createWorkflowStore(await mkdtemp(join(tmpdir(),'khu-refresh-'))),p=profile();let calls=0;
 const r={flow_id:'flow-refresh',state:'ANALYZED',input:{...input(),execution_target:'local'},application_profile:p,analysis:{mode:'llm',actual_model:'qwen3.5:4b'},selection_version:0,hashes:{profile:digest(p)},history:[],candidates:{all:[],primary:[]},...override};
 await store.write(r);
 const clients={recommend:async value=>{calls++;return localRecommendation(value.request_id);},profile:async()=>{throw Error('analysis_must_not_repeat');}};
 const server=createPlatformServer({store,clients,catalog,snapshots});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
 const send=async body=>{const response=await fetch(`http://127.0.0.1:${server.address().port}/api/v1/flows/flow-refresh/resource-refresh`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {status:response.status,data:await response.json()};};
 return {store,r,clients,send,calls:()=>calls};
}
test('resource refresh resumes the same analysed flow without another LLM call',async t=>{
 const h=await fixture(t),result=await h.send({selection_version:0});assert.equal(result.status,200);
 const r=result.data;assert.equal(r.flow_id,h.r.flow_id);assert.deepEqual(r.application_profile,h.r.application_profile);assert.deepEqual(r.analysis,h.r.analysis);assert.equal(r.hashes.profile,h.r.hashes.profile);assert.equal(r.selection_version,1);assert.equal(r.candidates.primary[0].candidate_id,'khu-local-pc');assert.equal(h.calls(),1);assert.equal(r.history.at(-1).event,'resources_refreshed');
});
test('a blocked resource remains excluded with its actual shortage after refresh',async t=>{
 const h=await fixture(t);h.clients.recommend=async value=>({...localRecommendation(value.request_id),recommendations:[],excluded_resources:[{resource_id:'khu-local-pc',failures:[{code:'MEMORY_AVAILABLE_INSUFFICIENT',field:'memory.available_mib',required:2048,actual:906.93}]}]});
 const result=await h.send({selection_version:0});assert.equal(result.status,200);assert.equal(result.data.candidates.primary.length,0);assert.equal(result.data.candidates.discovered_count,1);assert.equal(result.data.candidates.excluded[0].failures[0].actual,906.93);
});
test('refresh rejects stale selections, generated plans and actual executions before querying resources',async t=>{
 for(const patch of [{selection_version:2},{geon:{guard:{status:'APPROVED'}}},{state:'PLAN_UNKNOWN'},{execution:{status:'RUNNING'}},{input:{execution_target:'cloud'}}]){
  const h=await fixture(t,patch),result=await h.send({selection_version:0});assert.equal(result.status,409);assert.equal(h.calls(),0);
 }
});
test('a mismatched resource response never overwrites the current flow',async t=>{
 const h=await fixture(t);h.clients.recommend=async value=>({...localRecommendation(value.request_id),profile_id:'another-profile'});
 const result=await h.send({selection_version:0});assert.equal(result.status,422);assert.equal((await h.store.read(h.r.flow_id)).selection_version,0);
});
test('a plan created while resources are queried cannot be replaced by the refresh',async t=>{
 const h=await fixture(t);h.clients.recommend=async value=>{await h.store.update(h.r.flow_id,r=>({...r,state:'PLAN_PENDING'}));return localRecommendation(value.request_id);};
 const result=await h.send({selection_version:0});assert.equal(result.status,409);assert.equal((await h.store.read(h.r.flow_id)).state,'PLAN_PENDING');
});
