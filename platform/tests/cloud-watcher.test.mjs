import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {watcherHealth} from '../server/cloud-services.mjs';
test('watcher PID reuse or different lock owner cannot authorize creation',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-watch-'));t.after(()=>rm(dir,{recursive:true,force:true}));await mkdir(join(dir,'watcher.lock'));
 const owner={pid:42,nonce:'owned',process_started_at:'exact-start',executable:'owned-node'};await writeFile(join(dir,'watcher.lock/owner.json'),JSON.stringify(owner));await writeFile(join(dir,'watcher.json'),JSON.stringify({...owner,schema_version:1,ready:true,heartbeat_at:new Date().toISOString()}));
 assert.equal((await watcherHealth(dir,{inspect:async()=>({process_started_at:'reused',executable:'owned-node'})})).healthy,false);
 assert.equal((await watcherHealth(dir,{inspect:async()=>({process_started_at:'exact-start',executable:'owned-node'})})).healthy,true);
 await writeFile(join(dir,'watcher.lock/owner.json'),JSON.stringify({...owner,nonce:'different'}));assert.equal((await watcherHealth(dir,{inspect:async()=>owner})).healthy,false);
});
