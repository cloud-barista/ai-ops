import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {createPlatformServer} from '../server/index.mjs';
import {catalog,snapshots} from './fixtures.mjs';
const scores=await import('../profile-llm.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const runners=await import('../server/llm-comparison.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const candidate={candidate_id:'qwen3.5-profile-generator',actual_model:'qwen3.5:4b'};
function reply(id='cpu-online',model=candidate){
  const values={
    'cpu-online':{cpu:2,memory:2048,storage:20,gpu:false,count:0,vram:0,max:3,p95:50,rps:0,pattern:'ONLINE'},
    'gpu-online':{cpu:8,memory:16384,storage:40,gpu:true,count:1,vram:16384,max:2,p95:150,rps:100,pattern:'ONLINE'},
    'cpu-batch':{cpu:4,memory:8192,storage:30,gpu:false,count:0,vram:0,max:1,p95:0,rps:0,pattern:'BATCH'}
  }[id];
  return {application_profile:{profile_id:'profile-'+id,app_id:'llm-eval-'+id,app_version:'1.0.0',workload:{task_type:'CLASSIFICATION',request_pattern:values.pattern,expected_rps:0},requirements:{compute:{cpu_cores_min:values.cpu,memory_mib_min:values.memory,storage_gib_min:values.storage},accelerator:{required:values.gpu,type:values.gpu?'GPU':'',count_min:values.count,memory_mib_min_per_device:values.vram},deployment:{replicas_min:1,replicas_max:values.max,isolation:'ONE_MAJOR_APP_PER_VM'},slo:{latency_p95_ms_max:values.p95,throughput_rps_min:values.rps},cost:{currency:'',cost_per_hour_max:0}},analysis:{confidence:0.9,assumptions:[],missing_fields:[],warnings:[]}},analysis:{mode:'llm',candidate_id:model.candidate_id,actual_model:model.actual_model,provider:'local-openai-compatible',latency_ms:100,attempts:1}};
}
function scenario(id='cpu-online'){assert.ok(scores.profileLlmScenarios,'The profile evaluation suite is missing');return scores.profileLlmScenarios.find(s=>s.id===id);}
test('profile scores retain missing zero and false fields as extraction errors',()=>{
  assert.equal(typeof scores.scoreProfileResult,'function');
  const response=reply();delete response.application_profile.requirements.accelerator.required;delete response.application_profile.requirements.accelerator.count_min;
  const result=scores.scoreProfileResult({candidate,scenario:scenario(),response,durationMs:123});
  assert.equal(result.matched_fields,11);assert.equal(result.total_fields,13);assert.equal(result.exact_match,false);
  assert.deepEqual(result.fields.filter(f=>!f.matched).map(f=>f.path),['requirements.accelerator.required','requirements.accelerator.count_min']);
});
test('optional empty accelerator type and unspecified SLO use the formal Go JSON defaults',()=>{
  const response=reply();delete response.application_profile.requirements.accelerator.type;delete response.application_profile.requirements.slo.throughput_rps_min;
  const result=scores.scoreProfileResult({candidate,scenario:scenario(),response,durationMs:100});
  assert.equal(result.exact_match,true);assert.equal(result.matched_fields,13);
  response.application_profile.requirements.slo.throughput_rps_min=null;
  assert.equal(scores.scoreProfileResult({candidate,scenario:scenario(),response,durationMs:100}).matched_fields,12);
});
test('rule fallback and foreign model outputs are never counted as successful LLM responses',()=>{
  assert.equal(typeof scores.scoreProfileResult,'function');
  for(const altered of [{mode:'local_rule'},{actual_model:'other'},{candidate_id:'other'}]){
    const response=reply();Object.assign(response.analysis,altered);
    const result=scores.scoreProfileResult({candidate,scenario:scenario(),response,durationMs:100});
    assert.equal(result.contract_valid,false);assert.equal(result.matched_fields,0);assert.equal(result.exact_match,false);
  }
  const response=reply();response.application_profile.app_id='unrelated';
  assert.equal(scores.scoreProfileResult({candidate,scenario:scenario(),response,durationMs:100}).contract_valid,false);
});
test('summary ranks actual extraction success before speed and cannot recommend wholly failed models',()=>{
  assert.equal(typeof scores.summarizeProfileComparison,'function');
  const llama={candidate_id:'llama3.2-profile-generator',actual_model:'llama3.2:3b'};
  const slow=scores.scoreProfileResult({candidate,scenario:scenario(),response:reply(),durationMs:1000});
  const response=reply('cpu-online',llama);response.application_profile.requirements.compute.cpu_cores_min=99;
  const fast=scores.scoreProfileResult({candidate:llama,scenario:scenario(),response,durationMs:10});
  const summary=scores.summarizeProfileComparison([slow,fast],[candidate,llama],1);
  assert.equal(summary.recommended_candidate_id,'qwen3.5-profile-generator');assert.equal(summary.candidates[0].field_accuracy_percent,100);
  assert.equal(summary.candidates[1].matched_fields,12);assert.equal(summary.candidates[1].exact_count,0);
  const failed={...slow,contract_valid:false,matched_fields:0,exact_match:false,error_code:'provider_unavailable'};
  assert.equal(scores.summarizeProfileComparison([failed],[candidate],1).recommended_candidate_id,null);
});
async function runner(profile){
  assert.equal(typeof runners.createLlmComparison,'function');
  const directory=await mkdtemp(join(tmpdir(),'khu-llm-'));
  const store=createWorkflowStore(directory);
  return {store,directory,service:runners.createLlmComparison({store,clients:{profile,llmModels:async()=>({models:[{name:'qwen3.5:4b'},{name:'llama3.2:3b'}]})}})};
}
async function completed(service,id){
  for(let i=0;i<100;i++){const record=await service.get(id);if(record.state!=='RUNNING')return record;await new Promise(r=>setTimeout(r,5));}
  assert.fail('comparison did not reach a terminal state');
}
test('comparison calls the formal profile generator serially and saves six actual results',async()=>{
  let inFlight=0,peak=0;const seen=[];
  const h=await runner(async value=>{
    seen.push(value);peak=Math.max(peak,++inFlight);await new Promise(r=>setTimeout(r,2));inFlight--;
    const model=value.candidate_id==='qwen3.5-profile-generator'?candidate:{candidate_id:value.candidate_id,actual_model:'llama3.2:3b'};
    return reply(value.application.app_id.slice('llm-eval-'.length),model);
  });
  const started=await h.service.start({request_id:'serial-run-001'});const result=await completed(h.service,started.run_id);
  assert.equal(peak,1);assert.equal(result.state,'COMPLETED');assert.equal(result.completed_count,6);assert.equal(result.results.length,6);
  assert.equal(result.summary.candidates[0].matched_fields,39);assert.equal(result.summary.candidates[0].total_fields,39);
  assert.equal(result.summary.candidates[1].exact_count,3);
  assert.equal(seen[0].request,seen[3].request);assert.equal(seen[1].request,seen[4].request);assert.equal(seen[2].request,seen[5].request);
  assert.ok(seen.every(r=>r.input_type==='natural_language'&&!r.app_spec&&!('expected' in r)));
  assert.equal((await createWorkflowStore(h.directory).read(result.run_id)).results.length,6);
});
test('repeated submission of one request ID retrieves the receipt without another inference',async()=>{
  let count=0;const h=await runner(async value=>{count++;return reply(value.application.app_id.slice(9),{candidate_id:value.candidate_id,actual_model:value.candidate_id===candidate.candidate_id?candidate.actual_model:'llama3.2:3b'});});
  const first=await h.service.start({request_id:'duplicate-001'});await completed(h.service,first.run_id);
  const again=await h.service.start({request_id:'duplicate-001'});assert.equal(again.run_id,first.run_id);assert.equal(count,6);
});
test('old saved comparisons are re-scored with JSON defaults without editing receipts or calling a model',async()=>{
  let calls=0;const h=await runner(async()=>{calls++;return reply();});
  const response=reply();delete response.application_profile.requirements.accelerator.type;delete response.application_profile.requirements.slo.throughput_rps_min;
  const original={flow_id:'llm-old-score-001',run_id:'llm-old-score-001',state:'COMPLETED',created_at:'2026-10-05T00:00:00Z',candidates:[candidate],scenarios:[scenario()],results:[{candidate_id:candidate.candidate_id,scenario_id:'cpu-online',response,duration_ms:123,matched_fields:11}],summary:null};
  await h.store.write(original);
  const updated=await h.service.get(original.run_id);
  assert.equal(updated.evaluation_updated,true);assert.equal(updated.evaluation_version,scores.profileLlmEvaluationVersion);assert.equal(updated.summary.candidates[0].matched_fields,13);
  assert.deepEqual(updated.results[0].response,response);assert.equal(calls,0);assert.deepEqual(await h.store.read(original.run_id),original);
});
test('only one comparison can run and a restarted service identifies interrupted jobs',async()=>{
  let release;const blocked=new Promise(r=>release=r);const h=await runner(async value=>{await blocked;return reply(value.application.app_id.slice(9),{candidate_id:value.candidate_id,actual_model:value.candidate_id===candidate.candidate_id?candidate.actual_model:'llama3.2:3b'});});
  const first=await h.service.start({request_id:'exclusive-001'});
  await assert.rejects(()=>h.service.start({request_id:'exclusive-002'}),e=>e.status===409&&e.code==='llm_comparison_busy');
  const restart=runners.createLlmComparison({store:h.store,clients:{}});
  assert.equal((await restart.get(first.run_id)).state,'INTERRUPTED');
  release();await completed(h.service,first.run_id);
});
test('unavailable models and malformed comparison requests never trigger inference',async()=>{
  let count=0;const h=await runner(async()=>{count++;return reply();});
  await assert.rejects(()=>h.service.start({request_id:'../escape'}),e=>e.status===400);
  await assert.rejects(()=>h.service.start({request_id:'invalid-0001',scenarios:[]}),e=>e.status===400);
  const service=runners.createLlmComparison({store:h.store,clients:{profile:async()=>{count++;},llmModels:async()=>({models:[{name:'qwen3.5:4b'}]})}});
  await assert.rejects(()=>service.start({request_id:'missing-0001'}),e=>e.code==='llm_models_unavailable');assert.equal(count,0);
});
test('provider errors stay bounded while both candidates receive complete failure accounting',async()=>{
  const h=await runner(async()=>{throw Error('password=DO_NOT_EXPOSE');});
  const started=await h.service.start({request_id:'failed-run-001'});const result=await completed(h.service,started.run_id);
  assert.equal(result.state,'COMPLETED');assert.equal(result.completed_count,6);assert.equal(result.summary.recommended_candidate_id,null);
  assert.ok(!JSON.stringify(result).includes('DO_NOT_EXPOSE'));
  assert.ok(result.results.every(r=>r.error_code==='provider_unavailable'&&!r.contract_valid));
});
test('the platform exposes comparison progress separately from application flows',async t=>{
  const h=await runner(async value=>reply(value.application.app_id.slice(9),{candidate_id:value.candidate_id,actual_model:value.candidate_id===candidate.candidate_id?candidate.actual_model:'llama3.2:3b'}));
  const flowStore=createWorkflowStore(await mkdtemp(join(tmpdir(),'khu-llm-api-')));
  const server=createPlatformServer({clients:{},store:flowStore,catalog,snapshots,llmComparison:h.service});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const base='http://127.0.0.1:'+server.address().port;
  const options=await fetch(base+'/api/v1/llm-candidates');assert.equal(options.status,200);assert.equal((await options.json()).candidates.filter(c=>c.available).length,2);
  const started=await fetch(base+'/api/v1/llm-comparisons',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({request_id:'http-run-001'})});
  assert.equal(started.status,202);const run=await started.json();await completed(h.service,run.run_id);
  const reloaded=await fetch(base+'/api/v1/llm-comparisons/'+run.run_id);assert.equal(reloaded.status,200);assert.equal((await reloaded.json()).summary.candidates.length,2);
  assert.equal((await (await fetch(base+'/api/v1/flows')).json()).flows.length,0);
});
