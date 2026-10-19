import test from 'node:test';import assert from 'node:assert/strict';
import {createCloudClient,parseAwsPrice} from '../server/cloud-client.mjs';
import * as cloudClient from '../server/cloud-client.mjs';
test('bridge code and credentials travel in stdin below the Windows command-line limit',async()=>{
 assert.equal(typeof cloudClient.bridgeEnvelope,'function');
 const value=await cloudClient.bridgeEnvelope('guest_guard',{infra_id:'khu-poc-fixture',secret:'DO_NOT_PUT_IN_COMMAND'});
 assert.ok(Buffer.byteLength(value.command)<1024);
 assert.ok(!value.command.includes('DO_NOT_PUT_IN_COMMAND'));
 const body=JSON.parse(value.input);
 assert.equal(body.request.action,'guest_guard');
 assert.equal(body.request.input.infra_id,'khu-poc-fixture');
 assert.ok(!body.source.includes('__KHU_SSH_OBSERVER_SOURCE__'));
 const observer=body.source.match(/SSH_OBSERVER_B64='([A-Za-z0-9+/=]+)'/);
 assert.ok(observer);assert.ok(Buffer.from(observer[1],'base64').toString().includes('packet_metadata'));
});
test('a bridge failure retains only an allowlisted diagnostic and never provider stderr or credentials',async()=>{
 const script="process.stdin.resume();process.stdin.on('end',()=>{process.stdout.write(JSON.stringify({error:{code:'provider_unavailable',diagnostic_code:'guest_remote_assertion_failed'}}));process.stderr.write('private_key=DO_NOT_PRINT');process.exitCode=1;});";
 await assert.rejects(cloudClient.boundedProcess(process.execPath,['-e',script],''),e=>e.message==='provider_unavailable'&&e.diagnostic_code==='guest_remote_assertion_failed'&&!JSON.stringify(e).includes('DO_NOT_PRINT'));
 const c=createCloudClient({},async()=>{throw Object.assign(Error('password=hidden'),{diagnostic_code:'guest_ssh_authentication_failed'});});
 await assert.rejects(c.guestGuard({}),e=>e.message==='provider_unavailable'&&e.diagnostic_code==='guest_ssh_authentication_failed');
});

test('fixed bootstrap stage diagnostics survive the bridge and unknown remote text is discarded',async()=>{
 const c=createCloudClient({},async()=>{throw Object.assign(Error('private_key=DO_NOT_PRINT'),{diagnostic_code:'guest_bootstrap_process_start_failed'});});
 await assert.rejects(c.guestBootstrap({}),e=>e.message==='provider_unavailable'&&e.diagnostic_code==='guest_bootstrap_process_start_failed'&&!JSON.stringify(e).includes('DO_NOT_PRINT'));
 assert.equal(cloudClient.providerUnavailable('private_key=DO_NOT_PRINT').diagnostic_code,undefined);
});
test('SSH phase and reason survive process and transport boundaries with every other field discarded',async()=>{
 const diagnostic={phase:'COMMAND_SENT',reason:'RESET',stderr:'private_key=DO_NOT_PRINT',host:'34.1.2.3'};
 const script=`process.stdin.resume();process.stdin.on('end',()=>{process.stdout.write(JSON.stringify({error:{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:${JSON.stringify(diagnostic)}}}));process.exitCode=1;});`;
 await assert.rejects(cloudClient.boundedProcess(process.execPath,['-e',script],''),e=>{
  assert.deepEqual(e.ssh_diagnostic,{phase:'COMMAND_SENT',reason:'RESET'});assert.ok(!JSON.stringify(e).includes('DO_NOT_PRINT'));return true;
 });
 const c=createCloudClient({},async()=>{throw Object.assign(Error('hidden'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:diagnostic});});
 await assert.rejects(c.guestGuard({}),e=>{assert.deepEqual(e.ssh_diagnostic,{phase:'COMMAND_SENT',reason:'RESET'});return true;});
 assert.equal(cloudClient.providerUnavailable('guest_ssh_connection_failed',{phase:'private_key=DO_NOT_PRINT',reason:'RESET'}).ssh_diagnostic,undefined);
 assert.equal(cloudClient.providerUnavailable('unrecognized',{phase:'COMMAND_SENT',reason:'RESET'}).ssh_diagnostic,undefined);
});
test('SSH process allows the complete ownership and guest-command HTTP budgets to finish',()=>{
 assert.equal(typeof cloudClient.bridgeProcessTimeout,'function');
 // Bootstrap performs two ownership GETs (20s each), one command POST (70s),
 // and may need the configured 8s SSH connection timeout before Python starts.
 assert.ok(cloudClient.bridgeProcessTimeout('guest_bootstrap')>2*20000+70000+8000);
 assert.ok(cloudClient.bridgeProcessTimeout('guest_bootstrap_status')>20000+70000+8000);
 assert.ok(cloudClient.bridgeProcessTimeout('review')>60000+8000);
 assert.ok(cloudClient.bridgeProcessTimeout('credential')>3*20000+8000);
 assert.ok(cloudClient.bridgeProcessTimeout('create')>165000+8000);
 assert.throws(()=>cloudClient.bridgeProcessTimeout('unknown'),/unsupported/);
});
test('price selects exact Linux Oregon instance rather than a cheaper different region',()=>{
 const d={manifest:{hawkFilePublicationDate:'2026-10-05'},regions:{'US West (Oregon)':{a:{'Instance Type':'m6a.2xlarge','Operating System':'Linux',price:'0.3456'},b:{'Instance Type':'m6a.large','Operating System':'Linux',price:'0.0864'}}}};assert.equal(parseAwsPrice(d,'m6a.2xlarge'),'0.3456');assert.throws(()=>parseAwsPrice(d,'unknown'));
});
test('bridge accepts only configured lab SSH identity and masks provider secrets',async()=>{
 assert.throws(()=>createCloudClient({host:'other',ssh_key:'key'}),/fixed/);
 const seen=[];const c=createCloudClient({ssh_key:'key',image_ids:{cpu:'ami-cpu'},aws_profile:'ref'},async(action,input)=>{seen.push({action,input});if(action==='status')throw Error('credential=secret');return {ready:true};});await assert.rejects(c.status({infra_id:'khu-poc-test'}),e=>e.message==='provider_unavailable');assert.equal(seen.length,1);
});
