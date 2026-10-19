import test from 'node:test';import assert from 'node:assert/strict';import {cloudIntent,createCloudExecution} from '../server/cloud-execution.mjs';
const module=await import('../server/cloud-network.mjs').catch(()=>({}));
import {bridgeProcessTimeout} from '../server/cloud-client.mjs';
const input={flow_id:'flow-owned',selection_version:1,spec_sha256:'a'.repeat(64),candidate_id:'g6.2xlarge',region:'us-west-2',max_lifetime_seconds:2400};
test('availability zone is part of the immutable approved cloud intent',()=>{
 const a=cloudIntent(input,'run-owned'),b=cloudIntent({...input,availability_zone:'us-west-2b'},'run-owned');assert.equal(b.availability_zone,'us-west-2b');assert.notEqual(a.intent_sha256,b.intent_sha256);assert.throws(()=>cloudIntent({...input,availability_zone:'us-east-1a'},'run-owned'));
});
test('static network lookups fit inside the outer bridge process deadline',()=>{assert.ok(bridgeProcessTimeout('create',true)>=245000);assert.ok(bridgeProcessTimeout('network_review')>=80000);});
test('zone mismatch retains only an ownership-verified resource for cleanup',async()=>{
 for(const foreign of [false,true]){
  let value;const journal={write:async r=>(value=r),read:async()=>value,list:async()=>value?[value]:[],update:async(id,fn)=>(value=fn(value)),exclusive:async fn=>fn()};
  const client={review:async()=>({hourly_usd:'0.9776',source:'fixture',image_id:'ami-owned',checked_at:new Date().toISOString()}),preflight:async()=>({ready:true}),create:async i=>({infra_id:foreign?'foreign':i.infra_id,intent_sha256:foreign?'b'.repeat(64):i.intent_sha256,provider_instance_id:'i-0123456789abcdef0',availability_zone:'us-west-2a'})};
  const execution=createCloudExecution({journal,client,enabled:true,watcher:async()=>({healthy:true})});let r=await execution.review({...input,availability_zone:'us-west-2b'});await execution.approve(r.run_id,{cost_review_id:r.review.cost_review_id,intent_sha256:r.intent.intent_sha256});await execution.preflight(r.run_id);
  await assert.rejects(execution.create(r.run_id),foreign?/cloud_ownership_mismatch/:/cloud_zone_mismatch/);assert.equal(!!value.resource,!foreign);
 }
});
test('reused network binding requires exact zone, VPC, security group and key at AWS',async()=>{
 assert.equal(typeof module.auditNetworkBinding,'function');const binding={availability_zone:'us-west-2b',csp_subnet_id:'subnet-owned',csp_vpc_id:'vpc-owned',csp_security_group_id:'sg-owned',csp_key_name:'tb-key',vnet_id:'vnet',subnet_id:'subnet',security_group_id:'sg',ssh_key_id:'key',connection_name:'aws-us-west-2',ssh_user:'ubuntu'};let wrong=false;
 const provider=async(service,action)=>action==='describe-subnets'?{Subnets:[{SubnetId:binding.csp_subnet_id,VpcId:binding.csp_vpc_id,AvailabilityZone:wrong?'us-west-2a':'us-west-2b',State:'available'}]}:action==='describe-security-groups'?{SecurityGroups:[{GroupId:binding.csp_security_group_id,VpcId:binding.csp_vpc_id}]}:{KeyPairs:[{KeyName:binding.csp_key_name}]};
 assert.deepEqual(await module.auditNetworkBinding({intent:{availability_zone:'us-west-2b'},binding,provider}),binding);wrong=true;await assert.rejects(module.auditNetworkBinding({intent:{availability_zone:'us-west-2b'},binding,provider}),/network_binding_unverified/);
});
