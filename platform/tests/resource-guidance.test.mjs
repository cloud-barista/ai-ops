import test from 'node:test';
import assert from 'node:assert/strict';
import * as views from '../view-model.mjs';
const blocked={flow_id:'flow-memory',state:'ANALYZED',selection_version:0,input:{execution_target:'local'},application_profile:{},candidates:{all:[],primary:[],discovered_count:1,excluded:[{candidate_id:'khu-local-pc',failures:[{code:'MEMORY_AVAILABLE_INSUFFICIENT',field:'memory.available_mib',required:1024,actual:906.93}]}]}};
test('resource guidance reports discovered but excluded local capacity with units',()=>{
 assert.equal(typeof views.resourceAvailabilityModel,'function');const v=views.resourceAvailabilityModel(blocked);
 assert.equal(v.discovered,1);assert.equal(v.canRefresh,true);assert.equal(v.selectable,0);assert.match(v.issues[0],/메모리/);assert.match(v.issues[0],/1,024/);assert.match(v.issues[0],/906.93/);assert.match(v.issues[0],/MiB/);
});
test('blocked guidance resumes resource lookup without asking for another analysis',()=>{
 const v=views.workspaceGuidanceModel(blocked,{page:'infrastructure'});assert.equal(v.next.href,'#resources-refresh');assert.match(v.detail,/메모리/);assert.match(v.detail,/906.93/);
});
test('resource refresh is unavailable after a plan or execution exists',()=>{
 assert.equal(typeof views.resourceAvailabilityModel,'function');for(const patch of [{geon:{}},{execution:{}},{state:'PLAN_PENDING'}])assert.equal(views.resourceAvailabilityModel({...blocked,...patch}).canRefresh,false);
});
