import test from 'node:test';import assert from 'node:assert/strict';import {once} from 'node:events';
import {createPlatformServer} from '../server/index.mjs';
import {catalog,snapshots,clients} from './fixtures.mjs';
const group={group_id:'group-cloud',flow_id:'flow-cloud',target_profile_id:'khu-vm-abcdef12',operation_state:'IDLE',replicas:[{replica_id:'replica-cloud',deployment_id:'dep-cloud',runtime_id:'cpuvm-dep-cloud',status:'RUNNING',port:18160}]};
const model={model_id:'bert',model_revision:'pinned',batch_size:1,max_length:64};
const owned={flow_id:'flow-cloud',input:{model},cloud:{state:'RUNNING'},appdeploy:{target_profile_id:group.target_profile_id},replica_group:group,worker_bindings:{'replica-cloud':{pid:123,worker_started_at:100,device:'cpu'}}};
const metrics=(patch={})=>{
 const labels={flow_id:'flow-cloud',group_id:'group-cloud',replica_id:'replica-cloud',worker_pid:'123',worker_started_at:'100',device:'cpu',...Object.fromEntries(Object.entries(model).map(([k,v])=>[k,String(v)])),...patch};
 return 'khu_app_requests_total{'+Object.entries(labels).map(([k,v])=>k+'='+JSON.stringify(v)).join(',')+'} 64\n';
};
async function fixture(t,{receipt=owned,groups=[group],raw=metrics()}={}){
 let statusCalls=0,invocations=0;
 const server=createPlatformServer({catalog,snapshots,store:{read:async()=>receipt},clients:clients({groupList:async()=>groups,groupStatus:async()=>{statusCalls++;throw Error('expensive_live_probe_must_not_run');},replicaInvoke:async()=>{invocations++;return {status_code:200,raw_body:raw};}})});
 server.listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(resolve=>server.close(resolve)));
 return {get:()=>fetch(`http://127.0.0.1:${server.address().port}/api/v1/metrics/replicas/group-cloud/replica-cloud`),counts:()=>({statusCalls,invocations})};
}
test('cloud scrape uses current membership and bound worker metrics without a blocking runtime status probe',async t=>{
 const f=await fixture(t),r=await f.get();assert.equal(r.status,200);assert.equal(await r.text(),metrics());assert.deepEqual(f.counts(),{statusCalls:0,invocations:1});
});
for(const [name,patch] of [['other process',{worker_pid:'124'}],['restarted worker',{worker_started_at:'101'}],['other flow',{flow_id:'flow-other'}],['other replica',{replica_id:'replica-other'}],['other model',{model_revision:'other'}],['wrong device',{device:'cuda'}]])test('cloud scrape rejects '+name,async t=>{
 const f=await fixture(t,{raw:metrics(patch)}),r=await f.get();assert.equal(r.status,502);assert.equal((await r.json()).error.code,'metrics_unavailable');assert.equal(f.counts().statusCalls,0);
});
test('cloud scrape rejects stopped membership before invoking the worker',async t=>{
 const f=await fixture(t,{groups:[{...group,operation_state:'STOPPED'}]}),r=await f.get();assert.equal(r.status,409);assert.deepEqual(f.counts(),{statusCalls:0,invocations:0});
});
test('cloud scrape rejects mixed identities and missing identity labels',async t=>{
 for(const raw of [metrics()+metrics({worker_pid:'124'}),metrics().replace('worker_pid="123",','')]){const f=await fixture(t,{raw}),r=await f.get();assert.equal(r.status,502);}
});
