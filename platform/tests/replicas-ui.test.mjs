import test from 'node:test';
import assert from 'node:assert/strict';
import {replicaView,setupReplicaUI} from '../replicas-ui.mjs';
test('unknown group state does not present a missing or last recorded count as live zero',()=>{
  for(const actual_replicas of [null,2]){
    const v=replicaView({replica_group:{operation_state:'UNKNOWN',actual_replicas,applied_revision:2,replicas:[]}});
    assert.equal(v.actual,null);assert.equal(v.canApply,false);
  }
});
test('revision application uses the shared activity boundary until the server response completes',async t=>{
  const previous=globalThis.document;t.after(()=>{globalThis.document=previous;});
  const nodes=new Map();
  const element=()=>({hidden:false,disabled:false,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},replaceChildren(){},append(){}});
  globalThis.document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element};
  let record={flow_id:'flow-apply',selection_version:1,replica_group:{operation_state:'IDLE',actual_replicas:1,applied_revision:1,replicas:[]},feedback:{geon:{manifest_revisions:[{revision:2,desired_deployment_spec:{inference_configuration:{replicas:2}}}],operation_agent_execution:{scaling_guard:{status:'APPROVED'}}}}};
  let finish,busy=false;const calls=[],activity=[];
  const ui=setupReplicaUI({getReceipt:()=>record,isBusy:()=>busy,onReceipt:r=>{record=r;},api:async(...args)=>{calls.push(args);return new Promise(resolve=>{finish=resolve;});},run:async work=>{busy=true;activity.push('start');try{await work();}finally{busy=false;activity.push('end');}}});
  ui.render();const clicking=nodes.get('replica-apply').listeners.click();
  assert.deepEqual(activity,['start']);assert.equal(busy,true);assert.equal(calls.length,1);assert.equal(nodes.get('replica-apply').disabled,true);
  await nodes.get('replica-apply').listeners.click();assert.equal(calls.length,1);
  finish({...record,replica_group:{...record.replica_group,applied_revision:2,actual_replicas:2}});await clicking;
  assert.deepEqual(activity,['start','end']);assert.equal(busy,false);assert.equal(record.replica_group.actual_replicas,2);
});

test('uncertain application reaches shared recovery and cannot resubmit a stale revision',async t=>{
  const previous=globalThis.document;t.after(()=>{globalThis.document=previous;});
  const nodes=new Map(),element=()=>({hidden:false,disabled:false,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},replaceChildren(){},append(){}});
  globalThis.document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element};
  let record={flow_id:'flow-unknown',selection_version:1,replica_group:{operation_state:'IDLE',actual_replicas:1,applied_revision:1,replicas:[]},feedback:{geon:{manifest_revisions:[{revision:2,desired_deployment_spec:{inference_configuration:{replicas:2}}}],operation_agent_execution:{scaling_guard:{status:'APPROVED'}}}}};
  let posts=0,recovered=0,errors=0;
  const ui=setupReplicaUI({getReceipt:()=>record,onReceipt:r=>{record=r;},onError:()=>{errors++;},api:async()=>{posts++;throw Error('group_unknown');},run:async work=>{try{await work();}catch(e){assert.equal(e.message,'group_unknown');recovered++;record={...record,replica_group:{...record.replica_group,operation_state:'UNKNOWN'}};}}});
  ui.render();await nodes.get('replica-apply').listeners.click();
  assert.equal(recovered,1);assert.equal(errors,0);assert.equal(nodes.get('replica-apply').disabled,true);
  await nodes.get('replica-apply').listeners.click();assert.equal(posts,1);
});
