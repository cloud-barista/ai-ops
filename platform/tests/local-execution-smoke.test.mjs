import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {input} from './fixtures.mjs';
import {load} from './fixtures.mjs';
const deployment=await import('../deployment.mjs').catch(error=>{if(error.code==='ERR_MODULE_NOT_FOUND')return {};throw error;});
const active=process.env.KHU_SMOKE==='1',base='http://127.0.0.1:18100/api/v1';
async function request(path,body){const response=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const json=await response.json();assert.ok(response.ok,`${path}: ${JSON.stringify(json)}`);return json;}
for(const batchSize of [1,8])test(`AppDeploy runs pinned BERT Tiny batch ${batchSize}, binds GEON feedback and stops it`,{skip:!active},async()=>{
  const value=input(),options=await request('/execution-options');value.execution_target='local';value.application.app_id='local-smoke-'+Date.now();value.analysis_input.app_spec.app_id=value.application.app_id;value.analysis_input.app_spec.artifact=options.local.artifact;
  value.analysis_input.app_spec.memory_mib=1024;
  if(batchSize===8){const c=(await load('khu-cpu-policy.json')).cohorts.find(c=>c.model_id===value.model.model_id&&c.batch_size===8&&c.max_length===128);value.model.batch_size=8;value.model.max_length=128;value.workload={...value.workload,...c};}
  let r=await request('/flows',value);assert.equal(r.observation_origin,'observed');assert.equal(r.candidates.primary.length,1);assert.equal(r.candidates.primary[0].candidate_id,'khu-local-pc');assert.equal(r.candidates.primary[0].measurement,null);const id=r.flow_id;
  try{
    r=await request(`/flows/${id}/selection`,{candidate_id:'khu-local-pc',expected_selection_version:0});r=await request(`/flows/${id}/plan`,{selection_version:1});assert.equal(r.geon.guard.status,'APPROVED');
    assert.equal(typeof deployment.deploySelectedApp,'function');
    r=await deployment.deploySelectedApp(r,request);assert.ok(r.appdeploy.app_version_id);assert.equal(r.execution.status,'RUNNING');assert.equal(r.execution.target_profile_id,'khu-local-pc');
    for(const [route,body,code] of [['selection',{candidate_id:'khu-local-pc',expected_selection_version:1},'stop_local_worker_first'],['mock-executions',{selection_version:1},'real_execution_required'],['feedback',{selection_version:1,observation:{source:'demo_input'}},'measured_feedback_required']]){
      const blocked=await fetch(base+`/flows/${id}/${route}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});assert.ok(!blocked.ok);assert.equal((await blocked.json()).error.code,code);
    }
    const duplicate=await request(`/flows/${id}/appdeploy/submit`,{selection_version:1});assert.equal(duplicate.execution.execution_id,r.execution.execution_id);
    r=await request(`/flows/${id}/appdeploy/status`);assert.ok(r.appdeploy.logs);
    r=await request(`/flows/${id}/appdeploy/measure`,{selection_version:1});assert.equal(r.measurement.source,'local_probe');assert.equal(r.measurement.sample_count,64);assert.match(r.measurement.runtime_id,/^local-process-\d+$/);assert.ok(r.measurement.metrics.inference.latency_p95_ms>0);assert.ok(r.measurement.metrics.inference.throughput_rps>0);assert.equal(r.measurement.model.model_id,value.model.model_id);assert.equal(r.measurement.model.model_revision,value.model.model_revision);assert.equal(r.measurement.model.batch_size,batchSize);assert.equal(r.measurement.model.max_length,value.model.max_length);assert.equal(r.measurement.flow_id,id);assert.equal(r.measurement.input_count,64*batchSize);assert.equal(r.measurement.sample.logits[0].length,2);assert.ok(r.measurement.metrics.inference.model_inference_p95_ms>0);assert.equal(r.feedback_state,'RECORDED');assert.equal(r.feedback.messages.optimizationFeedback.data.optimization_feedback.deployment_id,r.execution.execution_id);assert.ok(r.feedback.geon.scaling_decision);
  }finally{
    const known=await request(`/flows/${id}`);if(known.execution?.status==='RUNNING')await request(`/flows/${id}/appdeploy/stop`,{selection_version:1});
  }
  const final=await request(`/flows/${id}`);assert.equal(final.execution.status,'STOPPED');assert.equal(final.execution.target_profile_id,final.selected_candidate_id);await writeFile('output/khu-platform-validation/appdeploy-smoke-receipt.json',JSON.stringify(final,null,2));
  await writeFile(`output/khu-platform-validation/bert-tiny-batch${batchSize}-smoke-receipt.json`,JSON.stringify(final,null,2));
  await assert.rejects(()=>fetch('http://127.0.0.1:18160/healthz',{signal:AbortSignal.timeout(1000)}));
});
