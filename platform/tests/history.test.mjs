import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {updateHistoryVisibility} from '../history.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {createPlatformServer} from '../server/index.mjs';
const sample=()=>({flow_id:'flow-history',trace_id:'trace-history',state:'SELECTED',selection_version:1,input:{analysis_input:{request:'CPU 분류 앱'}},history:[{event:'analysis_requested',at:'2026-10-01T08:00:00Z'},{event:'selected',candidate_id:'cpu8',at:'2026-10-01T08:00:01Z'},{event:'selected',candidate_id:'cpu8',at:'2026-10-01T08:00:01Z'}]});

test('deleting one of two identical events keeps stable identity, flow state and the other event',()=>{
  const r=sample();
  const {history_view,...deleted}=updateHistoryVisibility(r,{operation:'delete',event_id:'event-1'});
  assert.deepEqual(history_view.deleted_event_ids,['event-1']);
  assert.deepEqual(deleted,r);
});

test('delete-all survives reload, excludes future events, and restores without duplicates',async t=>{
  const dir=await mkdtemp(join(tmpdir(),'khu-history-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  const store=createWorkflowStore(dir);await store.write(sample());
  const server=createPlatformServer({store});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/api/v1/flows/flow-history/history`;
  const response=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operation:'delete_all'})});
  assert.equal(response.status,200);assert.deepEqual((await response.json()).history_view.deleted_event_ids,['event-0','event-1','event-2']);
  const reloaded=await createWorkflowStore(dir).read('flow-history');assert.deepEqual(reloaded.history,sample().history);
  await store.update('flow-history',r=>({...r,history:[...r.history,{event:'plan_requested',at:'2026-10-01T08:00:02Z',selection_version:1}]}));
  assert.deepEqual((await store.read('flow-history')).history_view.deleted_event_ids,['event-0','event-1','event-2']);
  const restored=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operation:'restore'})});
  assert.equal(restored.status,200);
  const receipt=await restored.json();
  assert.deepEqual(receipt.history_view.deleted_event_ids,[]);
  assert.equal(receipt.history.length,4);
});

test('unknown event ids and operations cannot change stored history',()=>{
  const r=sample();
  r.history.push({event:'internal_event',at:r.history[0].at},{event:'selected',at:'invalid'});
  for(const id of ['event-3','event-4','event-999']){
    assert.throws(()=>updateHistoryVisibility(r,{operation:'delete',event_id:id}),e=>e.code==='history_event_not_found');
  }
  assert.deepEqual(updateHistoryVisibility(r,{operation:'delete_all'}).history_view.deleted_event_ids,['event-0','event-1','event-2']);
  assert.throws(()=>updateHistoryVisibility(r,{operation:'remove_everything'}),e=>e.code==='invalid_history_operation');
  assert.equal(r.history.length,5);
});
