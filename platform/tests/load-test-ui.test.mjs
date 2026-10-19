import test from 'node:test';
import assert from 'node:assert/strict';
import {setupLoadTestUI} from '../replicas-ui.mjs';

function fixture(api){
 const nodes=new Map(),document={getElementById(id){if(!nodes.has(id))nodes.set(id,{disabled:false,listeners:{},addEventListener(event,fn){this.listeners[event]=fn;}});return nodes.get(id);}};
 let receipt={flow_id:'flow-ui',selection_version:1,input:{execution_target:'local'},execution:{status:'RUNNING'},replica_group:{group_id:'group-ui',operation_state:'IDLE',applied_revision:1}};
 const ui=setupLoadTestUI({document,getReceipt:()=>receipt,onReceipt:r=>receipt=r,api});ui.render();
 return {ui,nodes,receipt:()=>receipt};
}
test('load UI preserves request failure after releasing loading state',async t=>{
 const f=fixture(async()=>{throw Error('running_local_group_required');});t.after(f.ui.dispose);
 await f.nodes.get('load-test-start').listeners.click();
 assert.match(f.nodes.get('load-test-status').textContent,/못했습니다/);
 assert.equal(f.nodes.get('load-test-start').disabled,false);
});
test('load UI sends only its selected flow and enables stop after starting',async t=>{
 const calls=[],f=fixture(async(path,body)=>{calls.push({path,body});return {...f.receipt(),load_test:{state:'RUNNING',completed_requests:0}};});t.after(f.ui.dispose);
 await f.nodes.get('load-test-start').listeners.click();
 assert.equal(calls[0].path,'/flows/flow-ui/load-test');assert.equal(calls[0].body.selection_version,1);
 assert.equal(f.nodes.get('load-test-start').disabled,true);assert.equal(f.nodes.get('load-test-stop').disabled,false);
 await f.nodes.get('load-test-stop').listeners.click();assert.equal(calls[1].path,'/flows/flow-ui/load-test/stop');
});
