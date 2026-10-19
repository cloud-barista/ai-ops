import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {loadValidationResults} from '../server/validation-results.mjs';
import {loadPolicyValidation} from '../server/policy-validation.mjs';

test('a new checkout reports absent original evidence instead of a successful historical execution',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'khu-public-checkout-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const cloud=await loadValidationResults({directory});
 assert.ok(cloud.results.every(r=>r.status==='unavailable'));
 assert.equal(cloud.read_only,true);
 const policy=await loadPolicyValidation({directory});
 assert.equal(policy.status,'unavailable');
 assert.equal(policy.execution_verified,false);
});
