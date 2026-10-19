import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pipelineEvents,pipelineViewModel} from '../pipeline.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {createPlatformServer} from '../server/index.mjs';
const history=await import('../history.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const sample=()=>({flow_id:'flow-history',trace_id:'trace-history',state:'SELECTED',selection_version:1,input:{analysis_input:{request:'CPU 분류 앱'}},history:[{event:'analysis_requested',at:'2026-10-01T08:00:00Z'},{event:'selected',candidate_id:'cpu8',at:'2026-10-01T08:00:01Z'},{event:'selected',candidate_id:'cpu8',at:'2026-10-01T08:00:01Z'}]});

test('deleting one of two identical events keeps stable identity, flow state and the other event',()=>{
  assert.equal(typeof history.updateHistoryVisibility,'function');
  const r=sample(),before=pipelineEvents(r),v=pipelineViewModel(r);
  assert.notEqual(before[1].id,before[2].id);
  const deleted=history.updateHistoryVisibility(r,{operation:'delete',event_id:before[1].id});
  assert.deepEqual(pipelineEvents(deleted).map(e=>e.id),[before[0].id,before[2].id]);
  assert.deepEqual(deleted.history,r.history);assert.deepEqual(pipelineViewModel(deleted),v);
  assert.deepEqual(pipelineEvents(history.updateHistoryVisibility(deleted,{operation:'restore'})),before);
});

test('delete-all survives reload, excludes future events, and restores without duplicates',async t=>{
  const dir=await mkdtemp(join(tmpdir(),'khu-history-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  const store=createWorkflowStore(dir);await store.write(sample());
  const server=createPlatformServer({store});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/api/v1/flows/flow-history/history`;
  const response=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operation:'delete_all'})});
  assert.equal(response.status,200);assert.deepEqual(pipelineEvents(await response.json()),[]);
  const reloaded=await createWorkflowStore(dir).read('flow-history');assert.equal(reloaded.history.length,3);assert.deepEqual(pipelineEvents(reloaded),[]);
  await store.update('flow-history',r=>({...r,history:[...r.history,{event:'plan_requested',at:'2026-10-01T08:00:02Z',selection_version:1}]}));
  assert.equal(pipelineEvents(await store.read('flow-history')).length,1);
  const restored=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operation:'restore'})});
  assert.equal(restored.status,200);assert.equal(pipelineEvents(await restored.json()).length,4);
});

test('unknown event ids and operations cannot change stored history',()=>{
  assert.equal(typeof history.updateHistoryVisibility,'function');const r=sample();
  assert.throws(()=>history.updateHistoryVisibility(r,{operation:'delete',event_id:'event-999'}),e=>e.code==='history_event_not_found');
  assert.throws(()=>history.updateHistoryVisibility(r,{operation:'remove_everything'}),e=>e.code==='invalid_history_operation');
  assert.equal(r.history.length,3);
});
test('a platform link restores the requested flow rather than another browser session record',()=>{
  assert.equal(typeof history.flowFromLocation,'function');
  assert.equal(history.flowFromLocation('http://127.0.0.1:18100/?flow=flow-shown-in-photo#deployment','flow-older'),'flow-shown-in-photo');
  assert.equal(history.flowFromLocation('http://127.0.0.1:18100/#overview','flow-older'),'flow-older');
  assert.equal(history.flowFromLocation('http://127.0.0.1:18100/?flow=../secret','flow-older'),null);
});
test('the main page prefers the running AWS experiment over a saved local or empty flow',()=>{
 assert.equal(typeof history.initialPlatformFlow,'function');const active=[{flow_id:'flow-aws',run_id:'run-aws',state:'RUNNING'}];
 assert.equal(history.initialPlatformFlow('http://localhost/','flow-local',active),'flow-aws');
 assert.equal(history.initialPlatformFlow('http://localhost/?flow=','flow-local',active),'flow-aws');
 assert.equal(history.initialPlatformFlow('http://localhost/?flow=flow-explicit','flow-local',active),'flow-explicit');
 assert.equal(history.initialPlatformFlow('http://localhost/?new=1','flow-local',active),null);
});
test('starting a new comparison clears the old flow link and preserves the UI version',()=>{
  assert.equal(typeof history.flowLocation,'function');
  const location='http://127.0.0.1:18100/?ui=integrated&flow=old#application';
  assert.equal(history.flowLocation(location,null),'/?ui=integrated#application');
  assert.equal(history.flowLocation(location,'new'),'/?ui=integrated&flow=new#application');
});

test('event details use the matching selection and historical status, and redact credentials',()=>{
  assert.equal(typeof history.historyEventDetails,'function');
  const r={...sample(),selection_version:2,geon:{decision:{action:'REJECT'}},selection_history:[{selection_version:1,geon:{decision:{action:'DEPLOY'},password:'private'},execution:{execution_id:'old-deployment'}}],history:[...sample().history.slice(0,2),{event:'plan_received',at:'2026-10-01T08:00:02Z',selection_version:1,action:'DEPLOY'},{event:'appdeploy_status',at:'2026-10-01T08:00:03Z',action:'RUNNING'},{event:'selected',candidate_id:'cpu32',at:'2026-10-01T08:00:04Z'}]};
  const events=pipelineEvents(r),plan=history.historyEventDetails(r,events[2].id),status=history.historyEventDetails(r,events[3].id);
  assert.equal(plan.payload.decision.action,'DEPLOY');assert.equal(plan.payload.password,'[redacted]');
  assert.equal(status.payload.status,'RUNNING');assert.equal(status.payload.execution_id,'old-deployment');
  assert.throws(()=>history.historyEventDetails(r,'event-999'),e=>e.code==='history_event_not_found');
});
