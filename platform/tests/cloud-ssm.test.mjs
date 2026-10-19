import test from 'node:test';import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const clientModule=await import('../server/ssm-client.mjs').catch(()=>({}));
const diagnosis=await import('../scripts/diagnose-cloud-ssm.mjs').catch(()=>({}));
const instance='i-0123456789abcdef0',command='11111111-2222-3333-4444-555555555555';
test('SSM association is scoped to the exact running instance and refuses an existing profile',async()=>{
 assert.equal(typeof clientModule.createSsmClient,'function');
 const calls=[];let existing=false;
 const c=clientModule.createSsmClient({},async(service,action,args)=>{calls.push({service,action,args});return action==='describe-instances'?{Reservations:[{Instances:[{InstanceId:instance,State:{Name:'running'},...existing?{IamInstanceProfile:{Arn:'existing'}}:{}}]}]}:{IamInstanceProfileAssociation:{InstanceId:instance,AssociationId:'iip-assoc-owned'}};});
 assert.equal((await c.attach({provider_instance_id:instance})).association_id,'iip-assoc-owned');
 assert.ok(calls[1].args.includes(instance));assert.ok(calls[1].args.includes('Name=AmazonSSMRoleForInstancesQuickSetup'));
 existing=true;await assert.rejects(c.attach({provider_instance_id:instance}),/profile_already_associated/);
 assert.equal(calls.filter(x=>x.action==='associate-iam-instance-profile').length,1);
});
test('SSM command output must match the exact owned instance and fixed Linux proof',async()=>{
 assert.equal(typeof clientModule.createSsmClient,'function');
 let reported=instance;const calls=[];
 const c=clientModule.createSsmClient({},async(service,action,args)=>{calls.push({action,args});if(action==='send-command')return {Command:{CommandId:command}};return {CommandId:command,InstanceId:reported,Status:'Success',ResponseCode:0,StandardOutputContent:JSON.stringify({probe:'KHU_SSM_PROBE_V1',os:'Linux',machine:'x86_64'}),StandardErrorContent:'SECRET'};});
 assert.equal(await c.sendProbe({provider_instance_id:instance}),command);
 assert.deepEqual((await c.commandStatus({provider_instance_id:instance},command)).proof,{os:'Linux',machine:'x86_64'});
 assert.ok(calls[0].args.includes('AWS-RunShellScript'));reported='i-fffffffffffffffff';
 await assert.rejects(c.commandStatus({provider_instance_id:instance},command),/command_identity_mismatch/);
});
function fixture(){
 let tick=Date.now(),run={run_id:'run-fixture',state:'REVIEWED',intent:{flow_id:'flow-fixture',candidate_id:'m6a.2xlarge',max_lifetime_seconds:900},review:{max_compute_usd:.0864,cost_review_id:'review',intent_sha256:'hash'}},calls=[];
 const services={config:{enabled:false},journal:{read:async()=>run},execution:{approve:async()=>calls.push('approve'),preflight:async()=>calls.push('preflight'),create:async()=>{calls.push('create');run={...run,state:'CREATED',resource:{provider_instance_id:instance},deadline_at:new Date(tick+900000).toISOString()};return run;},cleanup:async()=>{calls.push('cleanup');run={...run,state:'CLEANED',cleanup:{confirmed:true,attachments:{verified:true,remaining:[]}},last_provider_status:{instance_id:instance,state:'Terminated'}};return run;}}};
 return {prepared:{flow_id:'flow-fixture',cloud:structuredClone(run)},services,api:async()=>({enabled:false,pending_cleanup:[]}),ssm:{attach:async()=>({association_id:'owned'}),online:async()=>true,sendProbe:async()=>command,commandStatus:async()=>({state:'Success',proof:{os:'Linux',machine:'x86_64'}})},approved:true,computeBudgetUsd:.0864,verifyIdle:async()=>true,clock:()=>tick,pause:async ms=>{tick+=ms;},calls};
}
test('SSM connectivity proof is never an application deployment claim and always cleans the owned VM',async()=>{
 assert.equal(typeof diagnosis.executeSsmDiagnosis,'function');const f=fixture();
 const r=await diagnosis.executeSsmDiagnosis(f.prepared,f);
 assert.equal(r.ssm_verified,true);assert.equal(r.cloud_deployment_verified,false);assert.equal(r.cleanup_verified,true);assert.equal(r.passed,true);assert.deepEqual(f.calls,['approve','preflight','create','cleanup']);
});
test('missing approval or insufficient budget cannot create a VM',async()=>{
 assert.equal(typeof diagnosis.executeSsmDiagnosis,'function');
 for(const changed of [{approved:false},{computeBudgetUsd:.01}]){const f=fixture();await assert.rejects(diagnosis.executeSsmDiagnosis(f.prepared,{...f,...changed}));assert.equal(f.calls.length,0);}
});
test('SSM registration timeout terminates the same VM without replacement',async()=>{
 assert.equal(typeof diagnosis.executeSsmDiagnosis,'function');const f=fixture();f.ssm.online=async()=>false;
 const r=await diagnosis.executeSsmDiagnosis(f.prepared,{...f,onlineTimeoutMs:20000});
 assert.equal(r.ssm_verified,false);assert.equal(r.error_code,'ssm_online_timeout');assert.equal(r.cleanup_verified,true);assert.equal(f.calls.filter(x=>x==='create').length,1);
});
test('an unknown AWS error field cannot leak through the saved SSM failure report',async()=>{
 const f=fixture();f.ssm.attach=async()=>{throw Object.assign(Error('ssm_provider_unavailable'),{aws_code:'PRIVATE_SECRET'});};
 const r=await diagnosis.executeSsmDiagnosis(f.prepared,f);assert.equal(r.failure.aws_code,undefined);assert.equal(r.cleanup_verified,true);
});
test('a large fixed guest command uses a parameters file and an uncertain send is never repeated',async()=>{
 let sends=0,path;const c=clientModule.createSsmClient({},async(service,action,args)=>{if(action==='send-command'){sends++;path=args[args.indexOf('--parameters')+1].slice(7);const value=JSON.parse(await readFile(path,'utf8'));assert.ok(value.commands[0].startsWith('sudo -n -u ubuntu'));assert.ok(value.commands[0].length>35000);throw Error('ssm_provider_timeout');}throw Error('unexpected');});
 await assert.rejects(c.executeOwned({provider_instance_id:instance},{command:'fixed-command '+('x'.repeat(35000)),user:'ubuntu'},{deadlineAt:new Date(Date.now()+300000).toISOString()}),/ssm_provider_timeout/);assert.equal(sends,1);await assert.rejects(readFile(path),{code:'ENOENT'});
});
test('an SSM guest command cannot be sent when the remaining lifetime cannot fit its bounded execution',async()=>{
 let sends=0;const c=clientModule.createSsmClient({},async()=>{sends++;});
 await assert.rejects(c.executeOwned({provider_instance_id:instance},{command:'fixed',user:'ubuntu'},{deadlineAt:new Date(Date.now()+35000).toISOString()}),/ssm_command_deadline_insufficient/);assert.equal(sends,0);
});
