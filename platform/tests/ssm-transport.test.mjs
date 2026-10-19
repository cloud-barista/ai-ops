import test from 'node:test';import assert from 'node:assert/strict';import net from 'node:net';
const module=await import('../server/ssm-relay.mjs').catch(()=>({}));
const guestModule=await import('../server/ssm-guest.mjs').catch(()=>({}));
const run={run_id:'run-owned',state:'PREPARING',deadline_at:new Date(Date.now()+600000).toISOString(),intent:{intent_sha256:'a'.repeat(64),infra_id:'khu-poc-owned'},resource:{host:'34.1.2.3',provider_instance_id:'i-0123456789abcdef0',infra_id:'khu-poc-owned',intent_sha256:'a'.repeat(64)},ssm:{online:true,association_id:'iip-assoc-owned'}};
test('SSM SSH relay resolves only an exact active owned VM on port 22',()=>{
 assert.equal(typeof module.resolveSsmRoute,'function');assert.equal(module.resolveSsmRoute([run],'34.1.2.3',22).run_id,run.run_id);
 for(const [runs,host,port] of [[[run],'34.1.2.4',22],[[run],'34.1.2.3',18160],[[{...run,state:'CLEANED'}],'34.1.2.3',22],[[{...run,ssm:undefined}],'34.1.2.3',22],[[{...run,deadline_at:new Date(0).toISOString()}],'34.1.2.3',22],[[{...run,resource:{...run.resource,intent_sha256:'b'.repeat(64)}}],'34.1.2.3',22],[[run,run],'34.1.2.3',22]])assert.throws(()=>module.resolveSsmRoute(runs,host,port),/owned_ssm_route_required/);
});
test('SSM SOCKS relay handles fragmented frames and forwards bytes only after ownership verification',async()=>{
 assert.equal(typeof module.startSsmRelay,'function');let verified=0;
 const echo=net.createServer(s=>s.pipe(s));await new Promise(r=>echo.listen(0,'127.0.0.1',r));
 const relay=await module.startSsmRelay({port:0,listRuns:async()=>[run],connect:async r=>{assert.equal(r.run_id,run.run_id);verified++;return net.connect(echo.address().port,'127.0.0.1');}});
 try{
  const s=net.connect(relay.address().port,'127.0.0.1');s.on('error',()=>{});
  const read=n=>new Promise((resolve,reject)=>{let b=Buffer.alloc(0);function take(v){b=Buffer.concat([b,v]);if(b.length>=n){s.off('data',take);resolve(b);}}s.on('data',take);s.once('error',reject);});
  let result=read(2);s.write(Buffer.from([5]));s.write(Buffer.from([1,0]));assert.deepEqual([...await result],[5,0]);
  result=read(10);s.write(Buffer.from([5,1,0,1,34,1]));s.write(Buffer.from([2,3,0,22]));assert.equal((await result)[1],0);
  result=read(5);s.write('hello');assert.equal((await result).toString(),'hello');assert.equal(verified,1);s.destroy();
 }finally{await relay.closeOwned();await new Promise(r=>echo.close(r));}
});
test('SSM guest preparation binds profile and commands to the journal identity and marks the transport',async()=>{
 assert.equal(typeof guestModule.createSsmGuestAdapter,'function');let value={...structuredClone(run),state:'CREATED',ssm:undefined},calls=[];
 const journal={list:async()=>[value],read:async()=>value,update:async(id,fn)=>(value=fn(value))};
 const adapter=guestModule.createSsmGuestAdapter({},{journal,invoke:async(action,input)=>{assert.equal(action,'guest_command_prepare');return {command:'fixed-'+input.guest_action,user:'ubuntu',provider_instance_id:run.resource.provider_instance_id};},ssm:{attach:async r=>{calls.push('attach');assert.equal(r.provider_instance_id,run.resource.provider_instance_id);return {association_id:'iip-assoc-owned'};},online:async()=>true,executeOwned:async(r,{command})=>({output:command==='fixed-guest_guard'?'KHU_GUEST_GUARD_ARMED':JSON.stringify({state:'READY',provider_instance_id:r.provider_instance_id,intent_sha256:run.intent.intent_sha256,execution_mode:'ssh_guest'})})}});
 const input={...value.intent,...value.resource,deadline_at:value.deadline_at};assert.equal((await adapter('guest_guard',input)).armed,true);assert.equal(value.ssm.online,true);
 value.state='PREPARING';const proof=await adapter('guest_bootstrap_status',input);assert.equal(proof.execution_mode,'ssm_guest');assert.equal(proof.management_transport,'ssm');assert.deepEqual(calls,['attach']);
 await assert.rejects(adapter('guest_bootstrap_status',{...input,provider_instance_id:'i-fffffffffffffffff'}),/owned_ssm_guest_required/);
 value.state='CLEANED';await assert.rejects(adapter('guest_guard',input),/owned_ssm_guest_required/);
});
test('cleanup racing guest command preparation prevents a mutating SSM send',async()=>{
 let value={...structuredClone(run)},sends=0;const journal={list:async()=>[value],read:async()=>value};
 const adapter=guestModule.createSsmGuestAdapter({},{journal,invoke:async()=>{value.state='CLEANUP_PENDING';return {command:'fixed-bootstrap',user:'ubuntu',provider_instance_id:run.resource.provider_instance_id};},ssm:{executeOwned:async()=>{sends++;return {output:'KHU_BOOTSTRAP_STARTED'};}}});
 await assert.rejects(adapter('guest_bootstrap',{...value.intent,...value.resource}),/owned_ssm_guest_required/);assert.equal(sends,0);
});
test('graceful relay control refuses active cloud runs and acknowledges session shutdown',async()=>{
 assert.equal(typeof module.startRelayControl,'function');let active=true,closed=0;
 const control=await module.startRelayControl({address:{host:'127.0.0.1',port:0},canClose:async()=>!active,close:async()=>{closed++;}});
 const address={host:'127.0.0.1',port:control.address().port};
 try{await assert.rejects(module.requestRelayClose(address),/relay_has_active_cloud_run/);assert.equal(closed,0);active=false;await module.requestRelayClose(address);assert.equal(closed,1);}finally{if(control.listening)await new Promise(r=>control.close(r));}
});
test('timer and explicit shutdown share serialized completion and preserve failed cleanup for retry',async()=>{
 assert.equal(typeof module.serialTunnelOperations,'function');let finish,stops=0;
 const lifecycle=module.serialTunnelOperations({start:async r=>({run_id:r.run_id}),stop:async()=>{stops++;await new Promise(r=>{finish=r;});}});
 await lifecycle.ensure(run);const first=lifecycle.close();await new Promise(r=>setImmediate(r));let secondDone=false;const second=lifecycle.close().then(()=>{secondDone=true;});await new Promise(r=>setImmediate(r));assert.equal(secondDone,false);assert.equal(lifecycle.peek().run_id,run.run_id);finish();await Promise.all([first,second]);assert.equal(stops,1);assert.equal(lifecycle.peek(),undefined);
 let fail=true;const failed=module.serialTunnelOperations({start:async r=>({run_id:r.run_id}),stop:async()=>{if(fail)throw Error('unconfirmed');}});await failed.ensure(run);await assert.rejects(failed.close(),/unconfirmed/);assert.equal(failed.peek().run_id,run.run_id);fail=false;await failed.close();assert.equal(failed.peek(),undefined);
});
test('a tunnel with failed ownership initialization cannot be reused as ready',async()=>{
 let starts=0,stops=0;const lifecycle=module.serialTunnelOperations({start:async r=>({run_id:r.run_id,ready:true,generation:++starts}),ready:async item=>{if(item.generation===1)throw Error('ownership_inspection_failed');},usable:item=>item.ready,stop:async()=>{stops++;}});
 await assert.rejects(lifecycle.ensure(run),/ownership_inspection_failed/);const result=await lifecycle.ensure(run);assert.equal(result.generation,2);assert.equal(stops,1);await lifecycle.close();
});
