import test from 'node:test';import assert from 'node:assert/strict';import {buildVmArtifact} from '../server/vm-artifact.mjs';
test('portable VM artifact has pinned model hashes and no Windows path in VM payload',async()=>{
 const a=await buildVmArtifact({model_id:'google/bert_uncased_L-2_H-128_A-2',model_revision:'30b0a37ccaaa32f332884b96992754e246e48c5f',batch_size:1,max_length:64});assert.match(a.sha256,/^[a-f0-9]{64}$/);assert.equal(a.manifest.device_scope,'cpu_or_cuda');assert.doesNotMatch(JSON.stringify(a.payload),/[A-Z]:[\\/]|geonhae/);assert.ok(a.payload.files['bert_worker.py']);
});
