import test from 'node:test';import assert from 'node:assert/strict';
import {createWorkflowRoutes} from '../server/workflow-routes.mjs';
function receipt(){return {flow_id:'flow-test',input:{execution_target:'cloud'},selection_version:2,history:[],application_profile:{requirements:{accelerator:{required:false},deployment:{replicas_max:1}}},candidates:{all:[{candidate_id:'cpu',name:'m6a.2xlarge',selectable:true,feasible:true,shape:{cpu_cores:8,memory_mib:32768,gpu_count:0}},{candidate_id:'gpu',name:'g6.2xlarge',selectable:true,feasible:true,shape:{cpu_cores:8,memory_mib:32768,gpu_count:1}}]}};}
test('old selectable snapshots cannot bypass the cloud execution contract at selection time',async()=>{
 let r=receipt();const before=structuredClone(r),route=createWorkflowRoutes();
 await assert.rejects(route({request:{method:'POST'},path:'/api/v1/flows/flow-test/selection',value:{candidate_id:'gpu',expected_selection_version:2},store:{update:async(_,fn)=>r=fn(r)},clients:{}}),/cloud_cpu_gpu_requirements_mismatch/);
 assert.deepEqual(r,before);
});
