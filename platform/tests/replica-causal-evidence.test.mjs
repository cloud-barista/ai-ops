import test from 'node:test';
import assert from 'node:assert/strict';
import {actualGroupStatus} from '../server/replica-execution.mjs';
import {toFeedbackMessages} from '../server/geon-adapter.mjs';
const at='2026-10-07T08:00:00Z';
function fixture(){
 const revision=n=>({revision:n,created_at:at,deployment_request:{message_id:'request-'+n}});
 const r={flow_id:'flow-causal',trace_id:'trace',selection_version:1,execution_mode:'local_process',geon:{decision:{decision_id:'decision'},manifest_revisions:[revision(1)]},feedback:{geon:{manifest_revisions:[revision(1),revision(2)]}},replica_group:{group_id:'group-test',operation_state:'IDLE',applied_revision:1,actual_replicas:1,target_profile_id:'khu-local-pc'}};
 return r;
}
const observation={source:'local_probe',started_at:'2026-10-07T08:00:01Z',ended_at:'2026-10-07T08:00:02Z',metrics:{resource:{cpu_average_percent:1,memory_peak_mib:260,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:5,throughput_rps:10,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
test('actual status acknowledges the applied revision rather than an unapplied proposal',()=>{
 const r=fixture(),status=actualGroupStatus(r,r.replica_group);assert.equal(status.causation_id,'request-1');
 r.replica_group.applied_revision=2;assert.equal(actualGroupStatus(r,r.replica_group).causation_id,'request-2');
});
test('measured feedback reuses the acknowledged status so earlier observations remain linked',()=>{
 const r=fixture(),ack=actualGroupStatus(r,r.replica_group);ack.data.deployment_status.updated_at=at;
 r.execution={mode:'local_process',execution_id:'group-test',acknowledgement:ack};
 const result=toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-07T08:00:03Z'});
 assert.equal(result.deploymentStatus.message_id,ack.message_id);assert.equal(result.optimizationFeedback.causation_id,ack.message_id);
 assert.equal(result.deploymentStatus.data.deployment_status.updated_at,at);
});
test('feedback from before the confirmed deployment is rejected',()=>{
 const r=fixture(),ack=actualGroupStatus(r,r.replica_group);ack.data.deployment_status.updated_at='2026-10-07T08:00:02Z';
 r.execution={mode:'local_process',execution_id:'group-test',acknowledgement:ack};
 assert.throws(()=>toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-07T08:00:03Z'}),e=>e.code==='observation_before_deployment');
});
test('older scraped feedback cannot replace a more recent inference observation',()=>{
 const r=fixture(),ack=actualGroupStatus(r,r.replica_group);ack.data.deployment_status.updated_at=at;
 r.execution={mode:'local_process',execution_id:'group-test',acknowledgement:ack};r.feedback.observation={ended_at:observation.ended_at};
 assert.throws(()=>toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-07T08:00:03Z'}),e=>e.code==='observation_not_newer');
});
