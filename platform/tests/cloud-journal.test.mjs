import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createCloudJournal} from '../server/cloud-journal.mjs';
test('journal survives reopen and cannot rewrite ownership or be removed by UI hiding',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-journal-'));t.after(()=>rm(dir,{recursive:true,force:true}));const a=createCloudJournal(dir);
 await a.write({run_id:'run-test',intent:{flow_id:'flow-test',infra_id:'khu-poc-test',intent_sha256:'a'.repeat(64)},state:'CREATE_UNKNOWN'});
 const b=createCloudJournal(dir);assert.equal((await b.read('run-test')).state,'CREATE_UNKNOWN');await assert.rejects(b.update('run-test',r=>({...r,intent:{...r.intent,infra_id:'other'}})),/immutable/);assert.equal((await b.list()).length,1);assert.equal(b.remove,undefined);
});
