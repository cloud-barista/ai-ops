import test from 'node:test';import assert from 'node:assert/strict';import {deploymentViewModel} from '../deployment.mjs';import {pipelineViewModel} from '../pipeline.mjs';
test('unknown actual deployment is not shown as completed or restartable',()=>{
 const r={input:{execution_target:'local'},execution:{status:'UNKNOWN'},appdeploy:{status:'UNKNOWN'}};assert.match(deploymentViewModel(r).description,/확인/);assert.equal(pipelineViewModel(r).stages[3].state,'attention');assert.equal(deploymentViewModel(r).canRestart,false);
});
test('cloud execution directs to owned lifecycle without offering local deploy',()=>{const r={input:{execution_target:'cloud'},cloud:{state:'PREPARING'}};const v=deploymentViewModel(r);assert.equal(v.status,'PREPARING');assert.equal(v.canDeploy,false);assert.match(v.title,/VM|클라우드/);});
