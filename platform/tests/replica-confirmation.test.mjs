import test from 'node:test';
import assert from 'node:assert/strict';
import * as routes from '../server/replica-execution.mjs';
const receipt=()=>({flow_id:'flow-confirm',selection_version:3,appdeploy:{app_version_id:'app-v1',status:'PENDING'},selected_candidate_id:'khu-local-pc',history:[]});
const group=()=>({flow_id:'flow-confirm',group_id:'group-confirm',app_version_id:'app-v1',target_profile_id:'khu-local-pc',operation_state:'IDLE',actual_replicas:1,applied_revision:1,membership_epoch:1,replicas:[{status:'RUNNING',runtime_id:'runtime-1'}]});
test('confirmed execution transitions are recorded once with the actual replica membership',()=>{
 assert.equal(typeof routes.confirmReplicaGroup,'function');
 let r=routes.confirmReplicaGroup(receipt(),group());
 assert.equal(r.history.at(-1).event,'group_running');assert.equal(r.history.at(-1).actual_replicas,1);
 r=routes.confirmReplicaGroup(r,group());assert.equal(r.history.length,1);
 r=routes.confirmReplicaGroup(r,{...group(),actual_replicas:2,applied_revision:2,membership_epoch:2});
 assert.equal(r.history.at(-1).event,'revision_applied');assert.equal(r.history.at(-1).revision,2);assert.equal(r.history.at(-1).actual_replicas,2);
 r=routes.confirmReplicaGroup(r,{...group(),operation_state:'STOPPED',actual_replicas:0,applied_revision:2,membership_epoch:3,replicas:[]});
 assert.equal(r.history.at(-1).event,'group_stopped');assert.equal(r.history.at(-1).actual_replicas,0);
 assert.equal(routes.confirmReplicaGroup(r,r.replica_group).history.length,3);
});
test('unknown status does not claim a successful deployment or cross-flow confirmation',()=>{
 assert.equal(typeof routes.confirmReplicaGroup,'function');
 assert.equal(routes.confirmReplicaGroup(receipt(),{...group(),operation_state:'UNKNOWN'}).history.length,0);
 assert.throws(()=>routes.confirmReplicaGroup(receipt(),{...group(),flow_id:'flow-other'}),{code:'group_identity_mismatch'});
});
