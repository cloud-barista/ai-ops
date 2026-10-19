import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createPlatformServer} from '../server/index.mjs';
import {catalog,snapshots,clients} from './fixtures.mjs';

test('HTTP discovery follows current owned group membership and removes stopped targets without file watches',async t=>{
 let groups=[{group_id:'group-http',target_profile_id:'khu-local-pc',operation_state:'IDLE',replicas:[{replica_id:'replica-a',status:'RUNNING',port:18160}]},{group_id:'group-old',target_profile_id:'khu-local-pc',operation_state:'UNKNOWN',replicas:[{replica_id:'replica-b',status:'RUNNING',port:18161}]}],calls=0;
 const server=createPlatformServer({catalog,snapshots,store:{},clients:clients({groupList:async()=>{calls++;return groups;}})});
 server.listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(resolve=>server.close(resolve)));
 const url=`http://127.0.0.1:${server.address().port}/api/v1/monitoring/targets`;
 let response=await fetch(url);assert.equal(response.status,200);assert.match(response.headers.get('content-type'),/application\/json/);assert.match(response.headers.get('cache-control'),/no-store/);
 assert.deepEqual(await response.json(),[{targets:['127.0.0.1:18160'],labels:{khu_group_id:'group-http',khu_replica_id:'replica-a'}}]);
 groups=[{...groups[0],operation_state:'STOPPED'}];response=await fetch(url);assert.equal(response.status,200);assert.deepEqual(await response.json(),[]);assert.equal(calls,2);
 assert.equal((await fetch(url,{method:'POST'})).status,405);
});

test('HTTP discovery retains the owned cloud metrics proxy and rejects out-of-range worker ports',async t=>{
 let groups=[{group_id:'group-cloud',target_profile_id:'khu-vm-abcdef12',operation_state:'IDLE',replicas:[{replica_id:'replica-cloud',status:'RUNNING',port:18162}]}];
 const server=createPlatformServer({catalog,snapshots,store:{},clients:clients({groupList:async()=>groups})});
 server.listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(resolve=>server.close(resolve)));
 const url=`http://127.0.0.1:${server.address().port}/api/v1/monitoring/targets`;
 assert.deepEqual(await(await fetch(url)).json(),[{targets:['127.0.0.1:18100'],labels:{khu_group_id:'group-cloud',khu_replica_id:'replica-cloud',__metrics_path__:'/api/v1/metrics/replicas/group-cloud/replica-cloud'}}]);
 groups[0].replicas[0].port=22;const response=await fetch(url);assert.equal(response.status,500);assert.deepEqual(await response.json(),{error:{code:'internal_error'}});
});
