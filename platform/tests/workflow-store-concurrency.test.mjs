import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';

async function fixture(t){
  const directory=await mkdtemp(join(tmpdir(),'khu-store-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  const store=createWorkflowStore(directory);
  await store.write({flow_id:'flow-a',count:0});
  return store;
}

test('reads wait for an earlier update of the same record',async t=>{
  const store=await fixture(t);
  let entered,release;
  const updating=new Promise(resolve=>entered=resolve);
  const blocked=new Promise(resolve=>release=resolve);
  const update=store.update('flow-a',async value=>{entered();await blocked;return {...value,count:1};});
  await updating;
  const read=store.read('flow-a');
  await new Promise(resolve=>setTimeout(resolve,20));
  release();
  await update;
  assert.equal((await read).count,1);
});

test('direct writes cannot be overwritten by an earlier pending update',async t=>{
  const store=await fixture(t);
  let entered,release;
  const updating=new Promise(resolve=>entered=resolve);
  const blocked=new Promise(resolve=>release=resolve);
  const update=store.update('flow-a',async value=>{entered();await blocked;return {...value,count:1};});
  await updating;
  const write=store.write({flow_id:'flow-a',count:2});
  await new Promise(resolve=>setTimeout(resolve,20));
  release();
  await Promise.all([update,write]);
  assert.equal((await store.read('flow-a')).count,2);
});

test('a failed operation does not block later access or unrelated records',async t=>{
  const store=await fixture(t);
  await assert.rejects(()=>store.read('../escape'),/invalid_flow_id/);
  await assert.rejects(()=>store.write({flow_id:'../escape'}),/invalid_flow_id/);
  await assert.rejects(store.update('flow-a',()=>{throw Error('rejected');}),/rejected/);
  await store.write({flow_id:'flow-a',count:3});
  await store.write({flow_id:'flow-b',count:4});
  assert.equal((await store.read('flow-a')).count,3);
  assert.equal((await store.read('flow-b')).count,4);
});
