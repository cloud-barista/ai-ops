import assert from 'node:assert/strict';
import test from 'node:test';
import {input,profile,recommendation} from './fixtures.mjs';
const adapter=await import('../server/geon-adapter.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
export function receipt(){return {flow_id:'flow-test',trace_id:'trace-test',input:input(),application_profile:profile(),resource_recommendation:recommendation('flow-test'),selection_version:1,selected_candidate_id:'demo-cpu8',candidates:{all:[{candidate_id:'demo-cpu8',selectable:true,spec_id:'aws+us-west-2+m6a.2xlarge'}]}};}
test('maps immutable external inputs preserving requirements and model revision',()=>{
  assert.equal(typeof adapter.toExternalFlow,'function');const r=receipt();const message=adapter.toExternalFlow({receipt:r,selectionVersion:1});
  assert.deepEqual(message.application_context.data.application_profile,r.application_profile);
  assert.equal(message.application_context.correlation_id,'flow-test-s1');
  assert.equal(message.application_context.data.model_recommendation.selected_model.model_version,r.input.model.model_revision);
  const rec=message.resource_recommendation.data.resource_recommendation;assert.equal(rec.selected_candidate_id,'demo-cpu8');assert.equal(rec.candidates.length,1);
  assert.equal(rec.candidates[0].scores.total,.9);assert.equal(rec.candidates[0].scores.cost_efficiency,0);assert.equal(rec.candidates[0].desired_infrastructure.storage_gib_per_node,20);
});
test('cannot plan unknown candidates or missing resource facts',()=>{
  assert.equal(typeof adapter.toExternalFlow,'function');const r=receipt();r.selected_candidate_id='other';assert.throws(()=>adapter.toExternalFlow({receipt:r,selectionVersion:1}));
  r.selected_candidate_id='demo-cpu8';delete r.resource_recommendation.recommendations[0].resource.storage;assert.throws(()=>adapter.toExternalFlow({receipt:r,selectionVersion:1}));
});
test('binding rejects stale version and a GEON-selected alternative',()=>{
  assert.equal(typeof adapter.assertPlanBinding,'function');const r=receipt();const f={correlation_id:'flow-test-s1',trace_id:'trace-test',profile_id:r.application_profile.profile_id,decision:{action:'DEPLOY',selected_candidate_id:'demo-cpu8'},deployment_plan:{selected_candidate_id:'demo-cpu8'}};
  adapter.assertPlanBinding({receipt:r,geonFlow:f,selectionVersion:1});
  assert.throws(()=>adapter.assertPlanBinding({receipt:{...r,selection_version:2},geonFlow:f,selectionVersion:1}));
  f.decision.selected_candidate_id='other';assert.throws(()=>adapter.assertPlanBinding({receipt:r,geonFlow:f,selectionVersion:1}));
});
