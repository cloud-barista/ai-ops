import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {input,profile,catalog,snapshots,clients} from './fixtures.mjs';
const api=await import('../server/index.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
const stores=await import('../server/workflow-store.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
async function harness(t,upstream=clients()){
  assert.equal(typeof api.createPlatformServer,'function','Local flow API is not implemented');
  const directory=await mkdtemp(join(tmpdir(),'khu-api-')); const store=stores.createWorkflowStore(directory);
  const server=api.createPlatformServer({clients:upstream,store,catalog,snapshots});
  await new Promise(r=>server.listen(0,'127.0.0.1',r)); t.after(()=>new Promise(r=>server.close(r)));
  const url=`http://127.0.0.1:${server.address().port}`;
  const send=async(path,body)=>{const r=await fetch(url+path,body===undefined?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return{status:r.status,data:await r.json()};};
  return{send,store,directory,url};
}
test('browser modules can load the shared cloud lifecycle display contract over HTTP',async t=>{
 const h=await harness(t),response=await fetch(h.url+'/cloud-state.mjs');assert.equal(response.status,200);assert.match(response.headers.get('content-type'),/javascript/);assert.match(await response.text(),/export function cloudPreparationFailed/);
});

test('cloud manual feedback cannot bypass real worker measurement',async t=>{
 let called=false;const h=await harness(t,clients({feedback:async()=>{called=true;throw Error('should not call');}}));
 await h.store.write({flow_id:'flow-cloud',input:{execution_target:'cloud'},selection_version:1,history:[],execution:{mode:'cloud_vm',status:'STOPPED'}});
 const result=await h.send('/api/v1/flows/flow-cloud/feedback',{selection_version:1,observation:{source:'cloud_probe'}});assert.equal(result.status,422);assert.equal(result.data.error.code,'measured_feedback_required');assert.equal(called,false);
});
test('flow calls real contracts in order and survives reload',async t=>{
  let supplied;const h=await harness(t,clients({recommend:async p=>{supplied=p;return(await clients().recommend(p));}}));
  const result=await h.send('/api/v1/flows',input());assert.equal(result.status,201);
  assert.deepEqual(supplied.application_profile,profile());assert.equal(supplied.request_id,result.data.flow_id);
  assert.equal(result.data.state,'ANALYZED');assert.equal(result.data.analysis.mode,'structured');
  assert.deepEqual(result.data.history.map(event=>event.event),['analysis_requested','profile_received','resource_received']);
  assert.equal(result.data.candidates.primary[0].candidate_id,'demo-cpu8');
  assert.match(result.data.hashes.profile,/^[a-f0-9]{64}$/);
  assert.equal((await stores.createWorkflowStore(h.directory).read(result.data.flow_id)).flow_id,result.data.flow_id);
});

test('changing VM archives the prior measurement and clears active observation and deployment',async t=>{
  const h=await harness(t,clients({recommend:async p=>({request_id:p.request_id,profile_id:'profile-tiny',
    recommendations:catalog.entries.map((s,i)=>({rank:i+1,score:90,resource:{resource_id:s.name,healthy:true,
      labels:{spec_id:s.spec_id},cpu:{logical_cores:s.cpu_cores,available_cores:s.cpu_cores},
      memory:{total_mib:s.memory_mib,available_mib:s.memory_mib},storage:{available_gib:100},accelerators:[]}}))})}));
  const created=await h.send('/api/v1/flows',input()), id=created.data.flow_id;
  await h.send(`/api/v1/flows/${id}/selection`,{candidate_id:'m6a.2xlarge',expected_selection_version:0});
  const measurement={source:'cloud_probe',sample_count:64,metrics:{inference:{latency_p95_ms:3}}};
  await h.store.update(id,r=>({...r,measurement,operation_observation_state:'GPU_METRICS_UNAVAILABLE',
    geon:{decision:{selected_candidate_id:'m6a.2xlarge'}},execution:{mode:'cloud_vm',status:'STOPPED'}}));
  const switched=await h.send(`/api/v1/flows/${id}/selection`,{candidate_id:'g6.2xlarge',expected_selection_version:1});
  assert.equal(switched.status,200);
  assert.equal(switched.data.selected_candidate_id,'g6.2xlarge');
  assert.equal(switched.data.geon,null);
  assert.equal(switched.data.execution,null);
  assert.equal(switched.data.measurement,null);
  assert.equal(switched.data.operation_observation_state,null);
  assert.deepEqual(switched.data.selection_history.at(-1).measurement,measurement);
});
test('upstream errors return safe code and stopped receipt',async t=>{
  const h=await harness(t,clients({profile:async()=>{throw Error('password=DO_NOT_EXPOSE');}}));
  const result=await h.send('/api/v1/flows',input());assert.equal(result.status,502);
  assert.equal(result.data.error.code,'provider_unavailable');assert.ok(!JSON.stringify(result.data).includes('DO_NOT_EXPOSE'));
  assert.equal((await h.store.read(result.data.flow_id)).state,'STOPPED');
});
test('identity mismatch stops before recommendation',async t=>{
  let called=false;const h=await harness(t,clients({profile:async()=>({application_profile:{...profile(),app_version:'other'},analysis:{mode:'structured'}}),recommend:async()=>{called=true;}}));
  const r=await h.send('/api/v1/flows',input());assert.equal(r.status,422);assert.equal(called,false);
});
test('invalid body has no upstream side effects',async t=>{
  let called=false;const h=await harness(t,clients({profile:async()=>{called=true;}}));
  assert.equal((await h.send('/api/v1/flows',{})).status,400);assert.equal(called,false);
  const r=await fetch(h.url+'/api/v1/flows',{method:'POST',body:'x'.repeat(131073)});assert.equal(r.status,413);
});
test('store serializes writes and rejects traversal IDs',async t=>{
  const h=await harness(t);const r=await h.send('/api/v1/flows',input());
  await Promise.all(Array.from({length:5},()=>h.store.update(r.data.flow_id,v=>({...v,counter:(v.counter??0)+1}))));
  assert.equal((await h.store.read(r.data.flow_id)).counter,5);
  await assert.rejects(()=>h.store.read('../secret'));
});
test('the served interface does not retain old menu code or panel layout in the browser cache',async t=>{
  const h=await harness(t);
  for(const name of ['index.html','app.js','navigation.mjs','research-theme.css','infrastructure-context.mjs']){
    const response=await fetch(h.url+'/'+name);assert.equal(response.status,200);
    assert.equal(response.headers.get('cache-control'),'no-store');
  }
});

test('natural-language analysis stops before recommending when LLM_Op falls back to rules',async t=>{
  let recommended=false;
  const h=await harness(t,clients({profile:async()=>({application_profile:profile(),analysis:{mode:'local_rule',fallback_reason:'invalid_model_output'}}),recommend:async()=>{recommended=true;}}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'Online CPU classification, 2 cores and 2GiB RAM.',application:value.application};
  const r=await h.send('/api/v1/flows',value);
  assert.equal(r.status,422);assert.equal(r.data.error.code,'llm_analysis_unavailable');assert.equal(recommended,false);
  const saved=await h.store.read(r.data.flow_id);assert.equal(saved.state,'STOPPED');assert.equal(saved.analysis.mode,'local_rule');assert.equal(saved.resource_recommendation,undefined);
});

test('actual LLM analysis is preserved through the natural-language flow',async t=>{
  let supplied;
  const h=await harness(t,clients({profile:async value=>{supplied=value;return {application_profile:profile(),analysis:{mode:'llm',actual_model:'qwen3.5:4b',provider:'local-openai-compatible'}};}}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'Online CPU classification, 2 cores and 2GiB RAM.',application:value.application};
  const r=await h.send('/api/v1/flows',value);assert.equal(r.status,201);assert.equal(supplied.request,value.analysis_input.request);assert.equal(r.data.analysis.mode,'llm');assert.equal(r.data.analysis.actual_model,'qwen3.5:4b');
});

test('invalid natural-language identity or empty request has no model side effects',async t=>{
  let called=false;const h=await harness(t,clients({profile:async()=>{called=true;}}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'',application:value.application};
  assert.equal((await h.send('/api/v1/flows',value)).status,400);assert.equal(called,false);
  value.analysis_input.request='CPU classifier';value.analysis_input.application={...value.application,app_id:'other'};
  assert.equal((await h.send('/api/v1/flows',value)).status,400);assert.equal(called,false);
});
test('a pinned Llama model reaches recommendation with its real analysis identity',async t=>{
  const h=await harness(t,clients({profile:async request=>({application_profile:profile(),analysis:{mode:'llm',candidate_id:request.candidate_id,actual_model:'llama3.2:3b'}})}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'CPU classification',candidate_id:'llama3.2-profile-generator',application:value.application};
  const r=await h.send('/api/v1/flows',value);assert.equal(r.status,201);assert.equal(r.data.analysis.candidate_id,'llama3.2-profile-generator');assert.equal(r.data.analysis.actual_model,'llama3.2:3b');
});
test('a response from a different LLM stops before infrastructure recommendation',async t=>{
  let recommended=false;
  const h=await harness(t,clients({profile:async()=>({application_profile:profile(),analysis:{mode:'llm',candidate_id:'qwen3.5-profile-generator',actual_model:'qwen3.5:4b'}}),recommend:async()=>{recommended=true;}}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'CPU classification',candidate_id:'llama3.2-profile-generator',application:value.application};
  const r=await h.send('/api/v1/flows',value);assert.equal(r.status,422);assert.equal(r.data.error.code,'llm_identity_mismatch');assert.equal(recommended,false);
});
test('unknown LLM candidate has no inference side effects',async t=>{
  let called=false;const h=await harness(t,clients({profile:async()=>{called=true;}}));
  const value=input();value.analysis_input={input_type:'natural_language',request:'CPU classification',candidate_id:'arbitrary-remote-model',application:value.application};
  assert.equal((await h.send('/api/v1/flows',value)).status,400);assert.equal(called,false);
});
