import test from 'node:test';import assert from 'node:assert/strict';
import {buildCandidates} from '../server/candidates.mjs';
import {profile,catalog,snapshots,recommendation} from './fixtures.mjs';
test('local execution uses observed PC resources without borrowing AWS measurements',()=>{
  const rec=recommendation('r');rec.recommendations[0].resource.resource_id='khu-local-pc';rec.recommendations[0].resource.provider='local';rec.recommendations[0].resource.labels={provider:'local'};
  const result=buildCandidates({applicationProfile:profile(),recommendation:rec,workload:{},catalog,...snapshots,executionTarget:'local'});
  assert.equal(result.primary.length,1);const c=result.primary[0];assert.equal(c.candidate_id,'khu-local-pc');assert.equal(c.origin,'observed');assert.equal(c.measurement,null);assert.equal(c.shape.cpu_cores,8);assert.equal(c.spec_id,'local+windows+pc');
});
test('local request with no observed matching target cannot substitute an AWS demo node',()=>{
  const result=buildCandidates({applicationProfile:profile(),recommendation:recommendation('r'),workload:{},catalog,...snapshots,executionTarget:'local'});assert.equal(result.primary.length,0);
});
