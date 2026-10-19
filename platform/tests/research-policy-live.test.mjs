import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {input} from './fixtures.mjs';
import {policyComparisonSummary} from '../panels.mjs';
const enabled=process.env.KHU_SMOKE==='1';
const root=new URL('../../output/khu-platform-validation/research-policy/',import.meta.url);
async function call(path,body){const response=await fetch('http://127.0.0.1:18100/api/v1'+path,body===undefined?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const result=await response.json();assert.ok(response.ok,JSON.stringify(result));return result;}

test('original artifacts and held-out cases feed real planning and operational shadow decisions',{skip:!enabled},async()=>{
  const report=JSON.parse(await readFile(new URL('research-cases.json',root),'utf8'));
  for(const origin of report.origins)assert.equal(createHash('sha256').update(await readFile(origin.path)).digest('hex'),origin.sha256);
  const groups=report.split_groups;
  for(const a of ['train','validation','test'])for(const b of ['train','validation','test'])if(a!==b)assert.ok(groups[a].every(g=>!groups[b].includes(g)));
  assert.ok(report.cases.every(c=>!groups.train.includes(c.source_scenario_id)));
  const value=input();value.application.app_id='research-policy-live-check';value.analysis_input.app_spec.app_id=value.application.app_id;
  let r=await call('/flows',value);const id=r.flow_id;
  r=await call(`/flows/${id}/selection`,{candidate_id:r.candidates.primary[0].candidate_id,expected_selection_version:0});
  r=await call(`/flows/${id}/plan`,{selection_version:1});
  assert.equal(r.geon.decision.action,'DEPLOY');assert.equal(r.geon.guard.status,'APPROVED');
  let assessment=r.geon.shadow_policy_assessments.at(-1);assert.equal(assessment.phase,'planning');assert.equal(assessment.model_version,'khu-policy-rf-v1');assert.match(assessment.artifact_sha256,/^[a-f0-9]{64}$/);
  r=await call(`/flows/${id}/mock-executions`,{selection_version:1});
  const now=new Date();const observation={source:'demo_input',started_at:new Date(now-60000).toISOString(),ended_at:now.toISOString(),metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:80,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  r=await call(`/flows/${id}/feedback`,{selection_version:1,observation});
  assessment=r.feedback.geon.shadow_policy_assessments.at(-1);assert.equal(assessment.phase,'operation');assert.equal(assessment.model_version,'khu-policy-rf-v1');
  for(const c of report.cases){
    r=await call(`/flows/${id}/research-policy`,{case_id:c.case_id,phase:c.phase,selection_version:1});
    const result=r.research_validations.at(-1).result;
    assert.equal(result.execution_performed,false);assert.equal(result.artifact_sha256,assessment.artifact_sha256);
    assert.ok(['validation','test'].includes(result.split));assert.equal(result.cost_basis,'original_cost_proxy_not_aws_bill');
    assert.equal(result.comparison.schema_version,'khu-policy-comparison/v1');
    const baseline=result.comparison.baseline,measured=c.rows.find(row=>row.candidate_id===baseline.candidate_id&&row.action===baseline.action&&row.desired_replicas===baseline.desired_replicas);
    assert.ok(measured);assert.equal(baseline.constraint_violation,!measured.feasible);assert.equal(baseline.reward,measured.reward);
    const validated=result.comparison.validated;
    if(validated.action==='ABSTAIN'){assert.equal(validated.agreement,null);assert.equal(validated.constraint_violation,null);}
    else{assert.equal(validated.constraint_violation,false);assert.equal(validated.agreement,result.agreement);}
  }
  const reloaded=await call(`/flows/${id}`);assert.equal(reloaded.research_validations.length,report.cases.length);
  assert.equal(reloaded.execution.mode,'mock');assert.equal(reloaded.execution.cloud_vm_created,false);
  const comparison=policyComparisonSummary(reloaded);assert.equal(comparison.caseCount,report.cases.length);
  await writeFile(new URL('live-verification.json',root),JSON.stringify({verified_at:new Date().toISOString(),origins_unchanged:true,holdout_groups_disjoint:true,case_count:report.cases.length,comparison,receipt:reloaded},null,2));
});
