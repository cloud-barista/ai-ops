import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,stat,utimes,readFile} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {syncReplicaTargets} from '../server/prometheus-targets.mjs';
test('unchanged discovery leaves the watched file intact while membership changes update targets',async()=>{
 const path=join(await mkdtemp(join(tmpdir(),'khu-discovery-')),'targets.json');
 const g={group_id:'group-test',target_profile_id:'khu-local-pc',operation_state:'IDLE',replicas:[{replica_id:'r1',status:'RUNNING',port:18160},{replica_id:'r2',status:'RUNNING',port:18161}]};
 await syncReplicaTargets([g],path);await utimes(path,new Date('2000-01-01'),new Date('2000-01-01'));
 const before=(await stat(path)).mtimeMs;await syncReplicaTargets([g],path);assert.equal((await stat(path)).mtimeMs,before);
 g.replicas[1].status='STOPPED';await syncReplicaTargets([g],path);const targets=JSON.parse(await readFile(path,'utf8'));
 assert.equal(targets.length,1);assert.deepEqual(targets[0].targets,['127.0.0.1:18160']);assert.notEqual((await stat(path)).mtimeMs,before);
});
