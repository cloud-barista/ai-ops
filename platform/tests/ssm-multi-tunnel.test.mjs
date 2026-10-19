import test from 'node:test';
import assert from 'node:assert/strict';
import * as relay from '../server/ssm-relay.mjs';
function manager(overrides={}){
 assert.equal(typeof relay.multiTunnelOperations,'function','independent multi-VM tunnels are required');
 const stopped=[];
 const lifecycle=relay.multiTunnelOperations({ports:[18197,18198],start:(run,port)=>({run_id:run.run_id,localPort:port,usable:true}),stop:async item=>stopped.push(item.run_id),...overrides});
 return {lifecycle,stopped};
}
test('two VM tunnels coexist without closing the first when the second connects',async()=>{
 const {lifecycle,stopped}=manager();const a=await lifecycle.ensure({run_id:'one'}),b=await lifecycle.ensure({run_id:'two'});
 assert.equal(a.localPort,18197);assert.equal(b.localPort,18198);assert.deepEqual(stopped,[]);assert.equal(lifecycle.items().length,2);
 assert.equal(await lifecycle.ensure({run_id:'one'}),a);
});
test('closing the second VM leaves the first tunnel available',async()=>{
 const {lifecycle,stopped}=manager();const a=await lifecycle.ensure({run_id:'one'});await lifecycle.ensure({run_id:'two'});await lifecycle.closeRun('two');
 assert.deepEqual(stopped,['two']);assert.equal(await lifecycle.ensure({run_id:'one'}),a);assert.equal(lifecycle.items().length,1);await lifecycle.close();assert.deepEqual(stopped,['two','one']);
});
test('a third concurrent VM is refused and existing tunnels are preserved',async()=>{
 const {lifecycle,stopped}=manager();await Promise.all([lifecycle.ensure({run_id:'one'}),lifecycle.ensure({run_id:'two'})]);await assert.rejects(lifecycle.ensure({run_id:'third'}),/ssm_tunnel_limit/);
 assert.deepEqual(stopped,[]);assert.equal(lifecycle.items().length,2);
});
test('failed shutdown retains the exact tunnel identity and its occupied port',async()=>{
 let fail=true;const {lifecycle}=manager({stop:async item=>{if(item.run_id==='one'&&fail)throw Error('uncertain shutdown');}});
 const one=await lifecycle.ensure({run_id:'one'});await lifecycle.ensure({run_id:'two'});await assert.rejects(lifecycle.closeRun('one'),/uncertain/);
 assert.ok(lifecycle.items().includes(one));await assert.rejects(lifecycle.ensure({run_id:'third'}),/ssm_tunnel_limit/);fail=false;await lifecycle.closeRun('one');assert.equal((await lifecycle.ensure({run_id:'third'})).localPort,18197);
});
test('closing all tunnels still closes the other VM if one shutdown fails',async()=>{
 const stopped=[];const {lifecycle}=manager({stop:async item=>{stopped.push(item.run_id);if(item.run_id==='one')throw Error('uncertain');}});await lifecycle.ensure({run_id:'one'});await lifecycle.ensure({run_id:'two'});
 await assert.rejects(lifecycle.close(),/uncertain/);assert.deepEqual(stopped,['one','two']);assert.deepEqual(lifecycle.items().map(x=>x.run_id),['one']);
});
test('failed readiness does not orphan a started SSM session',async()=>{
 const {lifecycle,stopped}=manager({ready:async()=>{throw Error('not ready');}});await assert.rejects(lifecycle.ensure({run_id:'one'}),/not ready/);assert.equal(lifecycle.items().length,1);await lifecycle.close();assert.deepEqual(stopped,['one']);
});
