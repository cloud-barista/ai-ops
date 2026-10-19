import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareValidation, executeValidation} from '../scripts/validate-cloud-vm.mjs';
import {buildCandidates} from '../server/candidates.mjs';
import {catalog,snapshots,profile} from './fixtures.mjs';

function fixture({kind='cpu',lostCreate=false,failedBootstrap=false,unverifiedCleanup=false,wrongDevice=false}={}) {
  const calls=[];
  let created=false, cleaned=false, tick=Date.now();
  const run={run_id:'run-test',state:'REVIEWED',intent:{flow_id:'flow-test',candidate_id:kind==='gpu'?'g6.2xlarge':'m6a.2xlarge',intent_sha256:'a'.repeat(64)},review:{cost_review_id:'review-test',image_id:'ami-123',ready_for_approval:true,max_compute_usd:kind==='gpu'?.7332:.2592,non_compute_margin_usd:1}};
  const receipt={flow_id:'flow-test',selection_version:1,input:{execution_target:'cloud'},geon:{decision:{action:'DEPLOY'},guard:{status:'APPROVED'}},candidates:{all:[{candidate_id:'candidate-test',name:run.intent.candidate_id,selectable:true}]},cloud:run};
  function running() {
    return {...receipt,cloud:{...run,state:cleaned?'CLEANED':failedBootstrap?'CLEANED':'RUNNING',resource:{provider_instance_id:'i-0123456789abcdef0'},app:{proof:{execution_mode:'ssh_guest',forward_finite:true,model_device:kind==='gpu'&&!wrongDevice?'cuda:0':'cpu',input_device:kind==='gpu'&&!wrongDevice?'cuda:0':'cpu',cuda_available:kind==='gpu'}}},execution:{status:'RUNNING',mode:'cloud_vm'},replica_group:{group_id:'group-test',membership_epoch:1,actual_replicas:1}};
  }
  const api=async(path,body)=>{
    calls.push({path,body});
    if(path==='/cloud/readiness')return {enabled:true,pending_cleanup:[],connection_configured:true,provider_audit_configured:true};
    if(path==='/execution-options')return {local:{artifact:{type:'SCRIPT',uri:'file:///fixture'}}};
    if(path==='/flows'||path.endsWith('/selection')||path.endsWith('/plan'))return receipt;
    if(path.endsWith('/cloud/review'))return receipt;
    if(path.endsWith('/cloud/approve'))return {...receipt,cloud:{...run,state:'APPROVED'}};
    if(path.endsWith('/cloud/preflight'))return {...receipt,cloud:{...run,state:'READY',preflight:{ready:true}}};
    if(path.endsWith('/cloud/create')){created=true;if(lostCreate)throw Error('provider_unavailable');return receipt;}
    if(path.endsWith('/cloud/cleanup')) {
      cleaned=true;
      return {...receipt,cloud:{...run,state:unverifiedCleanup?'CLEANUP_PENDING':'CLEANED',resource:{provider_instance_id:'i-0123456789abcdef0'},last_provider_status:{instance_id:'i-0123456789abcdef0',state:'Terminated'},cleanup:{confirmed:!unverifiedCleanup,attachments:{verified:!unverifiedCleanup,remaining:[]}}}};
    }
    if(path.endsWith('/appdeploy/measure'))return {...running(),measurement:{source:'cloud_probe',flow_id:'flow-test',sample_count:64,model:{model_id:'google/bert_uncased_L-2_H-128_A-2',model_revision:'30b0a37ccaaa32f332884b96992754e246e48c5f',batch_size:1,max_length:64},sample:{device:kind==='gpu'&&!wrongDevice?'cuda':'cpu',model_device:kind==='gpu'&&!wrongDevice?'cuda:0':'cpu',input_device:kind==='gpu'&&!wrongDevice?'cuda:0':'cpu'},metrics:{resource:{cpu_average_percent:5,memory_peak_mib:300,...kind==='gpu'?{accelerator_average_percent:10}:{}},inference:{latency_p95_ms:5,throughput_rps:1,error_rate_percent:0}}},feedback_state:'RECORDED',feedback:{geon:{scaling_decision:{action:'KEEP'},operation_agent_execution:{status:'completed',scaling_guard:{status:'APPROVED'}}}}};
    if(path.endsWith('/prometheus'))return {can_feedback:true,server_ready:true,observation:{source:'prometheus',flow_id:'flow-test',group_id:'group-test',membership_epoch:1,sample_count:10}};
    if(path==='/flows/flow-test')return created?running():receipt;
    throw Error('unexpected_path');
  };
  return {api,calls,receipt,clock:()=>tick,pause:async ms=>{tick+=ms;}};
}

test('preparation reviews the selected CPU or GPU and never approves or creates a VM',async()=>{
  for(const kind of ['cpu','gpu']) {
    const f=fixture({kind});const r=await prepareValidation(f.api,{kind});
    assert.equal(r.cloud.intent.candidate_id,kind==='gpu'?'g6.2xlarge':'m6a.2xlarge');
    assert.ok(f.calls.some(c=>c.path.endsWith('/cloud/review')));
    // A failed short attempt must not consume the time allowance reserved for
    // the successful CPU/GPU validation. Review each remaining VM for <=40min.
    assert.ok(f.calls.find(c=>c.path.endsWith('/cloud/review')).body.max_lifetime_seconds<=2400);
    assert.ok(!f.calls.some(c=>/\/(approve|create|cleanup)$/.test(c.path)));
  }
});
test('prepared workload is accepted by the real benchmark policy used by the platform',async()=>{
  const f=fixture();await prepareValidation(f.api,{kind:'cpu'});
  const submitted=f.calls.find(c=>c.path==='/flows').body;
  assert.doesNotThrow(()=>buildCandidates({applicationProfile:profile(),recommendation:{recommendations:[]},workload:submitted.workload,catalog,cpuPolicy:snapshots.cpuPolicy,evidence:snapshots.evidence}));
});
test('campaign preparation registers distinct app IDs without changing the pinned workload',async()=>{
 const f=fixture();await prepareValidation(f.api,{appId:'khu-campaign-one'});const submitted=f.calls.find(c=>c.path==='/flows').body;
 assert.equal(submitted.application.app_id,'khu-campaign-one');assert.equal(submitted.analysis_input.app_spec.app_id,'khu-campaign-one');assert.equal(submitted.model.model_revision,'30b0a37ccaaa32f332884b96992754e246e48c5f');
});
test('a short diagnosis reviews only the requested 15-minute lifetime without approving or creating',async()=>{
 const f=fixture();await prepareValidation(f.api,{kind:'cpu',lifetimeSeconds:900});
 assert.equal(f.calls.find(c=>c.path.endsWith('/cloud/review')).body.max_lifetime_seconds,900);
 assert.ok(!f.calls.some(c=>/\/(approve|create)$/.test(c.path)));
 for(const lifetimeSeconds of [0,899,2701,NaN]){
  const bad=fixture();await assert.rejects(prepareValidation(bad.api,{kind:'cpu',lifetimeSeconds}),/invalid_validation_lifetime/);assert.equal(bad.calls.length,0);
 }
});

test('a reviewed alternative GPU uses its exact candidate and a shorter lifetime',async()=>{
 const f=fixture({kind:'gpu'});f.receipt.candidates.all.push({candidate_id:'gpu-alternative',name:'g6.8xlarge',selectable:true});
 await prepareValidation(f.api,{kind:'gpu',candidateId:'g6.8xlarge',lifetimeSeconds:900});
 assert.equal(f.calls.find(c=>c.path.endsWith('/selection')).body.candidate_id,'gpu-alternative');
 assert.equal(f.calls.find(c=>c.path.endsWith('/cloud/review')).body.max_lifetime_seconds,900);
 for(const candidateId of ['m6a.2xlarge','g6.16xlarge']){const bad=fixture({kind:'gpu'});await assert.rejects(prepareValidation(bad.api,{kind:'gpu',candidateId}),/invalid_validation_candidate/);assert.equal(bad.calls.length,0);}
});

test('A10G fallback preparation keeps the original GPU requirements and exact selection',async()=>{
 const f=fixture({kind:'gpu'});f.receipt.candidates.all.push({candidate_id:'gpu-a10g',name:'g5.xlarge',selectable:true});await prepareValidation(f.api,{kind:'gpu',candidateId:'g5.xlarge',lifetimeSeconds:1800});
 assert.equal(f.calls.find(c=>c.path.endsWith('/selection')).body.candidate_id,'gpu-a10g');assert.equal(f.calls.find(c=>c.path==='/flows').body.analysis_input.app_spec.accelerator_memory_mib_per_device,16000);assert.equal(f.calls.find(c=>c.path.endsWith('/cloud/review')).body.max_lifetime_seconds,1800);
});
test('execution without human approval or with insufficient reviewed compute budget never mutates cloud state',async()=>{
  for(const approval of [{approved:false,computeBudgetUsd:1},{approved:true,computeBudgetUsd:.01}]) {
    const f=fixture();await assert.rejects(executeValidation(f.receipt,{api:f.api,...approval}),/approval|budget/);
    assert.equal(f.calls.filter(c=>c.body!==undefined).length,0);
  }
});
test('lost create response triggers exact-flow cleanup and never repeats creation',async()=>{
  const f=fixture({lostCreate:true});const r=await executeValidation(f.receipt,{api:f.api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause});
  assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);
  assert.equal(f.calls.filter(c=>c.path.endsWith('/cloud/create')).length,1);
  assert.ok(f.calls.some(c=>c.path==='/flows/flow-test/cloud/cleanup'));
});
test('bootstrap failure still cleans up and cannot be reported as successful deployment',async()=>{
  const f=fixture({failedBootstrap:true});const r=await executeValidation(f.receipt,{api:f.api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause});
  assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);
});
test('a failed measurement gathers guest evidence before exact VM cleanup',async()=>{
 const f=fixture(),events=[];
 const api=async(path,body)=>{if(path.endsWith('/appdeploy/measure'))throw Error('internal_error');if(path.endsWith('/cloud/cleanup'))events.push('cleanup');return f.api(path,body);};
 const r=await executeValidation(f.receipt,{api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause,diagnose:async({receipt,report})=>{assert.equal(receipt.flow_id,report.flow_id);events.push('diagnose');return {local_inference:{passed:8}};}});
 assert.deepEqual(events,['diagnose','cleanup']);assert.equal(r.runtime_diagnosis.local_inference.passed,8);assert.equal(r.cleanup_verified,true);
});
test('CPU and CUDA checks require actual inference, GEON feedback, Prometheus and provider-confirmed cleanup',async()=>{
  for(const kind of ['cpu','gpu']){
    const f=fixture({kind});const r=await executeValidation(f.receipt,{api:f.api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause});
    assert.equal(r.passed,true);assert.equal(r.cloud_deployment_verified,true);assert.equal(r.prometheus_verified,true);assert.equal(r.cleanup_verified,true);
  }
});
test('GPU proof on CPU cannot pass and is still cleaned up',async()=>{
  const f=fixture({kind:'gpu',wrongDevice:true});const r=await executeValidation(f.receipt,{api:f.api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause});
  assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);
});
test('actual SSM guest proof passes the same inference and cleanup requirements',async()=>{
 const f=fixture();const api=async(path,body)=>{const r=await f.api(path,body);if(r.cloud?.app?.proof)r.cloud.app.proof={...r.cloud.app.proof,execution_mode:'ssm_guest',management_transport:'ssm'};return r;};
 const r=await executeValidation(f.receipt,{api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause});assert.equal(r.passed,true);
});
test('metadata termination or missing attachment audit never proves cleanup',async()=>{
  const f=fixture({unverifiedCleanup:true});const r=await executeValidation(f.receipt,{api:f.api,approved:true,computeBudgetUsd:1,clock:f.clock,pause:f.pause,cleanupTimeoutMs:1000});
  assert.equal(r.passed,false);assert.equal(r.cleanup_verified,false);assert.equal(r.error_code,'cleanup_unconfirmed');
});
