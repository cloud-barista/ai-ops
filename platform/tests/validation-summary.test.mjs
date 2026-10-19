import test from 'node:test';
import assert from 'node:assert/strict';
import * as panels from '../panels.mjs';

function comparison(caseId,{artifact='a',phase='planning',version=1,abstain=false,violation=false,agreement=true}={}){
  return {selection_version:version,phase,result:{case_id:caseId,artifact_sha256:artifact,comparison:{schema_version:'khu-policy-comparison/v1',baseline:{action:'DEPLOY',agreement,regret:0,constraint_violation:violation},validated:{action:abstain?'ABSTAIN':'DEPLOY',agreement:abstain?null:agreement,regret:abstain?null:0,constraint_violation:abstain?null:false}}}};
}

test('comparison summary counts latest unique cases and shows abstention separately from agreement',()=>{
  assert.equal(typeof panels.policyComparisonSummary,'function');
  const r={selection_version:1,research_validations:[comparison('one'),comparison('two',{abstain:true,violation:true}),comparison('one',{agreement:false}),comparison('old',{version:0}),{...comparison('legacy'),result:{case_id:'legacy',agreement:true}}]};
  const summary=panels.policyComparisonSummary(r,'planning');
  assert.equal(summary.caseCount,2);
  assert.deepEqual(summary.baseline,{decisions:2,abstentions:0,agreement:1,constraintViolations:1});
  assert.deepEqual(summary.validated,{decisions:1,abstentions:1,agreement:0,constraintViolations:0});
  assert.equal(panels.policyComparisonSummary(r,'operation').caseCount,0);
});

test('comparison summary does not mix artifacts from different training runs',()=>{
  assert.equal(typeof panels.policyComparisonSummary,'function');
  const r={selection_version:1,research_validations:[comparison('before',{artifact:'old'}),comparison('after',{artifact:'new'})]};
  const summary=panels.policyComparisonSummary(r);
  assert.equal(summary.caseCount,1);
  assert.equal(summary.artifactSha256,'new');
});

test('operation history preserves measured inputs and deduplicates the current feedback',()=>{
  assert.equal(typeof panels.operationHistoryModel,'function');
  const feedback=(id,cpu,latency,action,current,desired)=>({messages:{optimizationFeedback:{data:{optimization_feedback:{feedback_id:id,deployment_id:'dep-a'}}}},observation:{source:'demo_input',ended_at:'2026-10-02T07:00:00Z',metrics:{resource:{cpu_average_percent:cpu},inference:{latency_p95_ms:latency}}},geon:{scaling_decision:{action,current_replicas:current,desired_replicas:desired}}});
  const high=feedback('high',85,80,'SCALE_OUT',1,2),low=feedback('low',10,5,'SCALE_IN',2,1);
  const r={execution:{execution_id:'dep-a'},feedback_history:[high,low],feedback:low};
  const rows=panels.operationHistoryModel(r);
  assert.equal(rows.length,2);
  assert.equal(rows[0].decision,'확장');
  assert.equal(rows[0].replicas,'1 → 2');
  assert.equal(rows[0].cpu,'85 %');
  assert.equal(rows[0].latency,'80 ms');
  assert.equal(rows[1].source,'데모 관측 입력');
  r.feedback_history.unshift(feedback('other',90,100,'SCALE_OUT',1,2));r.feedback_history[0].messages.optimizationFeedback.data.optimization_feedback.deployment_id='dep-other';
  assert.equal(panels.operationHistoryModel(r).length,2);
});
