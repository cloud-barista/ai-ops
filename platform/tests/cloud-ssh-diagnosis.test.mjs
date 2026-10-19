import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createCloudJournal} from '../server/cloud-journal.mjs';import {createCloudExecution} from '../server/cloud-execution.mjs';
import {executeSshDiagnosis} from '../scripts/diagnose-cloud-ssh.mjs';
import * as diagnosis from '../scripts/diagnose-cloud-ssh.mjs';

async function fixture(t,{lostCreate=false,failedProbe=false,unverifiedCleanup=false}={}){
 const directory=await mkdtemp(join(tmpdir(),'khu-ssh-diagnosis-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const journal=createCloudJournal(directory),calls=[];let terminated=false,tick=Date.now(),resource;
 const client={review:async()=>({hourly_usd:'0.3456',source:'fixture',checked_at:new Date().toISOString(),image_id:'ami-fixture'}),preflight:async()=>({ready:true}),
  create:async i=>{calls.push('create');resource={infra_id:i.infra_id,intent_sha256:i.intent_sha256,provider_instance_id:'i-0123456789abcdef0',node_id:'app-1',spider_name:'owned-spider',connection_name:'aws-us-west-2',attachment_ids:{volumes:['vol-0123456789abcdef0'],elastic_ips:[],captured_at:'now'}};if(lostCreate)throw Error('response_lost');return resource;},
  status:async()=>({resource,state:'Running'}),providerStatus:async()=>({state:terminated?'Terminated':'Running',instance_id:resource.provider_instance_id}),terminate:async()=>{calls.push('terminate');terminated=true;},billableAttachments:async()=>({verified:!unverifiedCleanup,remaining:[]}),
  guestGuard:async()=>{calls.push('guard');return {armed:true};},
  guestSshProbe:async()=>{calls.push('probe');return {state:'COMPLETE',probes:[{name:'SHORT',command_bytes:46,elapsed_ms:3,status:'PASSED'},{name:'BOOTSTRAP_SIZE',command_bytes:35548,elapsed_ms:3,status:failedProbe?'FAILED':'PASSED',...failedProbe?{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET',stderr:'private_key=DO_NOT_PRINT'}}:{}}]};}};
 const execution=createCloudExecution({journal,client,enabled:true,watcher:async()=>({healthy:true})});
 const reviewed=await execution.review({flow_id:'flow-test',selection_version:1,spec_sha256:'a'.repeat(64),candidate_id:'m6a.2xlarge',region:'us-west-2',max_lifetime_seconds:900});
 const prepared={flow_id:'flow-test',cloud:reviewed};
 const api=async path=>{calls.push(path);if(path==='/cloud/readiness')return {enabled:false,pending_cleanup:[]};if(path.endsWith('/cloud/status'))return {};throw Error('unexpected_api');};
 return {prepared,services:{journal,client,execution},api,calls,approved:true,computeBudgetUsd:.0864,verifyIdle:async()=>true,clock:()=>tick,pause:async ms=>{tick+=ms;},cleanupTimeoutMs:10000};
}
test('diagnosis makes one CPU VM, records two read-only probes, then verifies deletion without any deployment success claim',async t=>{
 const f=await fixture(t),r=await executeSshDiagnosis(f.prepared,f);
 assert.equal(r.passed,true);assert.equal(r.ssh_probe_verified,true);assert.equal(r.cleanup_verified,true);assert.equal(r.cloud_deployment_verified,false);
 assert.equal(f.calls.filter(x=>x==='create').length,1);assert.deepEqual(f.calls.filter(x=>['guard','probe','terminate'].includes(x)),['guard','probe','terminate']);
 assert.equal(r.probes[1].command_bytes,35548);assert.equal((await f.services.journal.read(r.run_id)).state,'CLEANED');
});
test('approval, cost, and existing-work guards block a paid request',async t=>{
 for(const change of [{approved:false},{computeBudgetUsd:.01},{verifyIdle:async()=>false}]){
  const f=await fixture(t);await assert.rejects(executeSshDiagnosis(f.prepared,{...f,...change}),/approval|budget|runtime/);assert.equal(f.calls.includes('create'),false);
 }
});
test('a failed probe still deletes the VM, preserves only safe failure evidence, and creates no replacement',async t=>{
 const f=await fixture(t,{failedProbe:true}),r=await executeSshDiagnosis(f.prepared,f);
 assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);assert.equal(r.probes[1].ssh_diagnostic.phase,'TCP_CONNECTED');
 assert.equal(JSON.stringify(r).includes('DO_NOT_PRINT'),false);assert.equal(f.calls.filter(x=>x==='create').length,1);
 const stored=await f.services.journal.read(r.run_id);assert.equal(stored.failure.stage,'SSH_PROBE');assert.equal(stored.failure.ssh_diagnostic.reason,'RESET');
});
test('a lost create response is reconciled and cleaned without another create',async t=>{
 const f=await fixture(t,{lostCreate:true}),r=await executeSshDiagnosis(f.prepared,f);
 assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);assert.equal(f.calls.filter(x=>x==='create').length,1);assert.equal(f.calls.includes('probe'),false);
});
test('unverified provider attachments retain cleanup supervision and cannot pass',async t=>{
 const f=await fixture(t,{unverifiedCleanup:true}),r=await executeSshDiagnosis(f.prepared,f);
 assert.equal(r.cleanup_verified,false);assert.equal(r.passed,false);assert.equal(r.error_code,'cleanup_unconfirmed');
 assert.equal((await f.services.journal.read(r.run_id)).state,'CLEANUP_PENDING');
});
test('a changed journal price cannot consume an approval based on the earlier cheaper review',async t=>{
 const f=await fixture(t);await f.services.journal.update(f.prepared.cloud.run_id,r=>({...r,review:{...r.review,max_compute_usd:100}}));
 await assert.rejects(executeSshDiagnosis(f.prepared,f),/cost_review_changed/);assert.equal(f.calls.includes('create'),false);
});
test('an SSH guard failure is saved and cleaned before any probe is attempted',async t=>{
 const f=await fixture(t);f.services.client.guestGuard=async()=>{throw Object.assign(Error('provider_unavailable'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET'}});};
 const r=await executeSshDiagnosis(f.prepared,f);assert.equal(r.passed,false);assert.equal(r.cleanup_verified,true);assert.equal(r.failure.stage,'GUEST_GUARD');assert.equal(r.failure.ssh_diagnostic.phase,'TCP_CONNECTED');assert.equal(f.calls.includes('probe'),false);
});
test('low lab storage is reported by name and needs no VM cleanup because creation was never attempted',async t=>{
 const f=await fixture(t);f.services.client.preflight=async()=>({ready:false,checks:{openbao_unsealed:true,lab_root_free:false,provider_attachment_audit:true,password:'DO_NOT_PRINT'},storage:{lab_root:{path:'/var/tmp',free_bytes:4*1024**3,required_free_bytes:5*1024**3,secret:'DO_NOT_PRINT'}}});
 const r=await executeSshDiagnosis(f.prepared,f);
 assert.equal(r.error_code,'cloud_preflight_failed');assert.deepEqual(r.failure.failed_checks,['lab_root_free']);
 assert.equal(r.preflight.checks.openbao_unsealed,true);assert.equal(r.preflight.storage.lab_root.free_bytes,4*1024**3);
 assert.equal(r.vm_create_requested,false);assert.equal(r.cleanup_required,false);assert.equal(r.cleanup_state,'NOT_REQUIRED');
 assert.equal(f.calls.includes('create'),false);assert.equal(f.calls.includes('terminate'),false);assert.ok(!JSON.stringify(r).includes('DO_NOT_PRINT'));
});
test('a transient journal read failure after VM creation cannot skip termination',async t=>{
 const f=await fixture(t),read=f.services.journal.read;let fail=false;
 f.services.journal.read=async id=>{if(fail){fail=false;throw Error('fixture_read_unavailable');}return read(id);};
 f.services.client.guestGuard=async()=>{fail=true;throw Error('provider_unavailable');};
 const r=await executeSshDiagnosis(f.prepared,f);assert.equal(r.cleanup_verified,true);assert.equal(r.cleanup_required,true);assert.equal(r.cleanup_state,'CONFIRMED');assert.ok(f.calls.includes('terminate'));
});
test('preflight-only check reports storage without approving, creating, or cleaning any cloud resource',async t=>{
 assert.equal(typeof diagnosis.checkSshDiagnosis,'function');
 const f=await fixture(t);f.services.client.preflight=async()=>({ready:false,checks:{openbao_unsealed:true,lab_root_free:false},storage:{lab_root:{path:'/var/tmp',free_bytes:4*1024**3,required_free_bytes:5*1024**3}}});
 const r=await diagnosis.checkSshDiagnosis(f.prepared,{...f,watcher:async()=>({healthy:true})});
 assert.equal(r.vm_create_requested,false);assert.equal(r.ready,false);assert.deepEqual(r.failed_checks,['lab_root_free']);
 assert.equal((await f.services.journal.read(f.prepared.cloud.run_id)).state,'REVIEWED');assert.equal(f.calls.includes('create'),false);assert.equal(f.calls.includes('terminate'),false);
});

test('service storage failure is preserved when root storage passes, with no create and no secret diagnostic fields',async t=>{
 const f=await fixture(t);f.services.client.preflight=async()=>({ready:false,checks:{lab_root_free:true,lab_service_storage_free:false},storage:{lab_root:{path:'/var/tmp',free_bytes:6*1024**3,required_free_bytes:5*1024**3},lab_services:{docker_data:{available:true,free_bytes:21*1024**2,required_free_bytes:5*1024**3,password:'DO_NOT_PRINT'},unknown_secret:'DO_NOT_PRINT'}}});
 const r=await executeSshDiagnosis(f.prepared,f);
 assert.deepEqual(r.failure.failed_checks,['lab_service_storage_free']);assert.equal(r.preflight.checks.lab_root_free,true);
 assert.equal(r.preflight.storage.lab_services.docker_data.free_bytes,21*1024**2);assert.ok(!JSON.stringify(r).includes('DO_NOT_PRINT'));
 assert.equal(r.vm_create_requested,false);assert.equal(r.cleanup_state,'NOT_REQUIRED');assert.equal(f.calls.includes('create'),false);
});
import * as sshDiagnosis from '../scripts/diagnose-cloud-ssh.mjs';
test('only identity-matched provider cleanup clears stale cloud groups from the idle guard',()=>{
 assert.equal(typeof sshDiagnosis.cloudGroupIsCleaned,'function');
 const id='12345678-1234-1234-1234-123456789abc',g={flow_id:'f',target_profile_id:'khu-vm-'+id,app_version_id:'a'},r={run_id:'run-'+id,intent:{flow_id:'f'},app:{target_profile_id:g.target_profile_id,app_version_id:'a'},state:'CLEANED',cleanup:{confirmed:true,attachments:{verified:true,remaining:[]}},resource:{provider_instance_id:'i-0123456789abcdef0'},last_provider_status:{state:'Terminated',instance_id:'i-0123456789abcdef0'}};
 assert.equal(sshDiagnosis.cloudGroupIsCleaned(g,r),true);
 assert.equal(sshDiagnosis.cloudGroupIsCleaned({...g,flow_id:'other'},r),false);
 assert.equal(sshDiagnosis.cloudGroupIsCleaned({...g,target_profile_id:'local-pc'},r),false);
 assert.equal(sshDiagnosis.cloudGroupIsCleaned(g,{...r,cleanup:{confirmed:true,attachments:{verified:false,remaining:[]}}}),false);
});
test('diagnostic run must confirm the guest observer before spending time on SSH probes',async t=>{
 const f=await fixture(t);f.services.client.guestGuard=async()=>({armed:true,observer_ready:false});
 const r=await executeSshDiagnosis(f.prepared,{...f,requireObserver:true});
 assert.equal(r.ssh_probe_verified,false);assert.equal(r.probes.length,0);
 assert.equal(r.failure.code,'guest_observer_unavailable');assert.equal(r.cleanup_verified,true);
});
test('initial console output is insufficient until it covers the actual failed probe time',()=>{
 assert.equal(typeof sshDiagnosis.consoleCoversProbe,'function');
 assert.equal(sshDiagnosis.consoleCoversProbe([{at:100}],101),false);
 assert.equal(sshDiagnosis.consoleCoversProbe([{at:100},{at:102}],101),true);
 assert.equal(sshDiagnosis.consoleCoversProbe([],101),false);
});
