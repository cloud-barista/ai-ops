import assert from 'node:assert/strict';import test from 'node:test';import {input} from './fixtures.mjs';import {writeFile,mkdir} from 'node:fs/promises';
const active=process.env.KHU_SMOKE==='1';const base='http://127.0.0.1:18100';
async function post(path,body){const response=await fetch(base+'/api/v1'+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const json=await response.json();assert.ok(response.ok,`${path}: ${JSON.stringify(json)}`);return json;}
test('real local modules join CPU, GPU and unsuitable scenarios without cloud operations',{skip:!active},async()=>{
  const output=[];
  for(const kind of ['cpu','gpu','excessive']){const value=input();value.application.app_id='smoke-'+kind;value.analysis_input.app_spec.app_id=value.application.app_id;if(kind==='gpu')Object.assign(value.analysis_input.app_spec,{accelerator_type:'GPU',accelerator_count:1,accelerator_memory_mib_per_device:16000});if(kind==='excessive')value.analysis_input.app_spec.cpu_cores=256;
    let r=await post('/flows',value);assert.equal(r.analysis.mode,'structured');assert.equal(r.observation_origin,'demo_input');assert.equal(r.resource_recommendation.profile_id,r.application_profile.profile_id);
    if(kind==='excessive'){assert.equal(r.candidates.primary.length,0);output.push(r);continue;}
    const selected=r.candidates.primary[0];assert.ok(selected);if(kind==='gpu')assert.equal(selected.shape.gpu_count,1);
    r=await post(`/flows/${r.flow_id}/selection`,{candidate_id:selected.candidate_id,expected_selection_version:0});
    r=await post(`/flows/${r.flow_id}/plan`,{selection_version:1});assert.equal(r.geon.decision.action,'DEPLOY');assert.equal(r.geon.guard.status,'APPROVED');assert.equal(r.geon.deployment_plan.selected_candidate_id,selected.candidate_id);
    r=await post(`/flows/${r.flow_id}/mock-executions`,{selection_version:1});assert.equal(r.execution.mode,'mock');assert.equal(r.execution.cloud_vm_created,false);
    const now=new Date();const observation={source:'demo_input',started_at:new Date(now-60000).toISOString(),ended_at:now.toISOString(),metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:85,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:80,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
    r=await post(`/flows/${r.flow_id}/feedback`,{selection_version:1,observation});assert.equal(r.feedback.geon.scaling_decision.action,'SCALE_OUT');
    observation.metrics.resource.cpu_average_percent=10;observation.metrics.resource.accelerator_average_percent=10;observation.metrics.inference.latency_p95_ms=5;
    r=await post(`/flows/${r.flow_id}/feedback`,{selection_version:1,observation});assert.equal(r.feedback.geon.scaling_decision.action,'SCALE_IN');
    observation.metrics.resource.cpu_average_percent=50;observation.metrics.resource.accelerator_average_percent=50;
    r=await post(`/flows/${r.flow_id}/feedback`,{selection_version:1,observation});assert.equal(r.feedback.geon.scaling_decision.action,'KEEP');output.push(r);
  }
  await mkdir('output/khu-platform-validation',{recursive:true});await writeFile('output/khu-platform-validation/smoke-receipts.json',JSON.stringify(output,null,2));
});
