import test from 'node:test';import assert from 'node:assert/strict';import * as ui from '../cloud-ui.mjs';const {cloudView}=ui;
test('cancelling VM shutdown leaves the current application and VM untouched',async()=>{
 const calls=[];
 const result=await ui.requestCloudCleanup(async(...args)=>{calls.push(args);}, {flow_id:'flow-recording'}, {confirm:()=>false});
 assert.equal(result,null);assert.deepEqual(calls,[]);
});
test('a terminated VM directs recording to a new experiment instead of reusing its immutable app binding',()=>{
 const r={input:{execution_target:'cloud'},geon:{guard:{status:'APPROVED'}},cloud:{state:'CLEANED',resource:{provider_instance_id:'i-0123456789abcdef0'},cleanup:{confirmed:true}}};
 assert.equal(cloudView(r,{enabled:true}).canReview,false);
});
test('confirmed shutdown targets exactly the currently displayed experiment',async()=>{
 const calls=[];let message='';
 const result=await ui.requestCloudCleanup(async(...args)=>{calls.push(args);return {stopping:true};}, {flow_id:'flow-recording'}, {confirm:text=>{message=text;return true;}});
 assert.match(message,/VM.*종료/);assert.deepEqual(calls,[['/flows/flow-recording/cloud/cleanup',{}]]);assert.deepEqual(result,{stopping:true});
});
test('configured credentials with execution disabled are distinguished from missing configuration',()=>{
 const r={input:{execution_target:'cloud'},geon:{guard:{status:'APPROVED'}},cloud:{state:'REVIEWED'}};
 const configured={enabled:false,connection_configured:true,provider_audit_configured:true};
 const v=cloudView(r,configured);
 assert.equal(v.executionAvailability,'disabled');assert.equal(v.canReview,true);
 assert.equal(v.canApprove,false);assert.equal(v.canCreate,false);
 assert.equal(cloudView(r,{enabled:false,connection_configured:false}).executionAvailability,'unconfigured');
 assert.equal(cloudView(r,{enabled:true}).executionAvailability,'enabled');
});
test('an old CPU profile with a GPU selection shows the mismatch before any cloud request',()=>{
 const r={input:{execution_target:'cloud'},geon:{guard:{status:'APPROVED'}},application_profile:{requirements:{accelerator:{required:false},deployment:{replicas_max:1}}},selected_candidate_id:'gpu',candidates:{all:[{candidate_id:'gpu',name:'g6.2xlarge'}]}};
 const v=cloudView(r,{enabled:true});assert.equal(v.canReview,false);
 assert.equal(v.runtimeIssue.code,'cloud_cpu_gpu_requirements_mismatch');assert.match(v.runtimeIssue.message,/CPU VM/);
});
test('cloud creation requires stored approval, enabled adapter and ready preflight',()=>{
 const r={input:{execution_target:'cloud'},cloud:{state:'READY',approval:{expires_at:new Date(Date.now()+60000).toISOString()},preflight:{ready:true}}};assert.equal(cloudView(r,{enabled:false}).canCreate,false);assert.equal(cloudView(r,{enabled:true}).canCreate,true);r.cloud.state='CREATE_UNKNOWN';assert.equal(cloudView(r,{enabled:true}).canCreate,false);assert.equal(cloudView(r,{enabled:true}).canCleanup,true);
});
test('incomplete configuration quote can be viewed but cannot be approved',()=>{
 const r={input:{execution_target:'cloud'},cloud:{state:'REVIEW_INCOMPLETE',review:{max_compute_usd:.2592}}};assert.equal(cloudView(r,{enabled:true}).canApprove,false);
});
test('unknown creation and cleanup after preparation failure do not imply successful app deployment',()=>{
  assert.equal(typeof ui.cloudProgress,'function');
  const r={cloud:{state:'CREATE_UNKNOWN',review:{hourly_usd:'.3456'},approval:{},preflight:{ready:true}}};
  assert.equal(ui.cloudProgress(r)[3].done,false);
  r.cloud.state='CLEANED';r.cloud.cleanup={confirmed:true};
  assert.equal(ui.cloudProgress(r)[3].done,false);assert.equal(ui.cloudProgress(r)[4].done,true);
});
test('an accepted deployment job is displayed as pending before the provider journal leaves READY',()=>{
 const r={input:{execution_target:'cloud'},cloud_job_requested:true,geon:{guard:{status:'APPROVED'}},cloud:{state:'READY',preflight:{ready:true},approval:{expires_at:new Date(Date.now()+60000).toISOString()}}};
 const v=cloudView(r,{enabled:true});assert.equal(v.state,'CREATE_REQUESTED');assert.equal(v.canCreate,false);assert.equal(v.canReview,false);assert.equal(v.canCleanup,true);
});

test('a failed preparation check can be retried within its unconsumed valid approval',()=>{
 const r={input:{execution_target:'cloud'},cloud:{state:'PREFLIGHT_FAILED',approval:{expires_at:new Date(Date.now()+60000).toISOString()},preflight:{ready:false}}};
 assert.equal(cloudView(r,{enabled:true}).canPreflight,true);
 r.cloud.approval.consumed_at=new Date().toISOString();assert.equal(cloudView(r,{enabled:true}).canPreflight,false);
 r.cloud.approval.consumed_at=undefined;r.cloud.approval.expires_at=new Date(Date.now()-60000).toISOString();assert.equal(cloudView(r,{enabled:true}).canPreflight,false);
});
test('another running VM blocks new creation with an explicit reason while analysis and cost review remain available',()=>{
 const r={flow_id:'flow-new',input:{execution_target:'cloud'},geon:{guard:{status:'APPROVED'}},cloud:{run_id:'run-new',state:'READY',approval:{expires_at:new Date(Date.now()+60000).toISOString()},preflight:{ready:true}}};
 const connection={enabled:true,pending_cleanup:[{run_id:'run-recording',flow_id:'flow-recording',state:'RUNNING'}]};
 const v=cloudView(r,connection);assert.equal(v.canCreate,false);assert.equal(v.blockedByOtherRun,true);assert.equal(v.canReview,true);
 r.cloud.state='APPROVED';assert.equal(cloudView(r,connection).canPreflight,false);
 connection.pending_cleanup=[{run_id:'run-new',flow_id:'flow-new',state:'RUNNING'}];assert.equal(cloudView(r,connection).blockedByOtherRun,false);
});
