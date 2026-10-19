import test from 'node:test';
import assert from 'node:assert/strict';
import {measureReplicaGroup} from '../server/replica-probe.mjs';

function fixture(extra=0,{restart=false,missing=0}={}){
 const model={model_id:'bert',model_revision:'pinned',batch_size:1,max_length:64};
 const child={replica_id:'replica-test',deployment_id:'dep-test',runtime_id:'cpuvm-dep-test',status:'RUNNING',port:18160};
 const group={group_id:'group-test',membership_epoch:1,operation_state:'IDLE',actual_replicas:1,replicas:[child]};
 const receipt={flow_id:'flow-test',input:{model},execution_mode:'cloud_vm',cloud:{intent:{candidate_id:'m6a.large'},review:{hourly_usd:0.1}},replica_group:group,worker_bindings:{'replica-test':{pid:123,worker_started_at:10}}};
 const identity={...model,flow_id:receipt.flow_id,group_id:group.group_id,replica_id:child.replica_id,pid:123,worker_started_at:10,device:'cpu',model_device:'cpu',model_inference:true};
 let snapshots=0,calls=0;
 const clients={replicaInvoke:async()=>({status_code:200,body:{...identity,worker_started_at:restart&&snapshots>0?11:10,host_cpu_cores:2,completed:100+(snapshots++?64+extra-missing:0),cpu_seconds:1+snapshots/100,memory_rss_mib:256}}),groupInvoke:async()=>{calls++;return {replica_id:child.replica_id,result:{status_code:200,body:{...identity,input_count:1,inference_ms:2,logits:[[0.1,0.2]],probabilities:[[0.4,0.6]],predictions:[1],token_counts:[7]}}};},groupStatus:async()=>group};
 return {receipt,clients,calls:()=>calls};
}
test('a probe remains valid when other real requests run in the same measurement window',async()=>{
 const f=fixture(16),o=await measureReplicaGroup(f.clients,f.receipt);
 assert.equal(f.calls(),64);assert.equal(o.sample_count,64);assert.equal(o.observed_completed_requests,80);assert.equal(o.other_completed_requests,16);assert.equal(o.replica_requests['replica-test'],64);
});
test('an isolated probe records no concurrent requests',async()=>{
 const f=fixture(),o=await measureReplicaGroup(f.clients,f.receipt);
 assert.equal(o.observed_completed_requests,64);assert.equal(o.other_completed_requests,0);
});
test('counter loss and worker restarts cannot be accepted as complete measurement',async()=>{
 for(const options of [{missing:1},{restart:true}]){const f=fixture(0,options);await assert.rejects(measureReplicaGroup(f.clients,f.receipt),{code:options.restart?'replica_identity_mismatch':'request_count_mismatch'});}
});
