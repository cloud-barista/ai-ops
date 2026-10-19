import nodeTest from 'node:test';
import {existsSync} from 'node:fs';
// Optional historical-evidence integration checks require the original files.
const evidenceAvailable=existsSync(new URL('../../output/khu-platform-validation/policy-runtime-fix-20261017/evaluation-2026-10-17T13-56-48.004Z/result.json',import.meta.url));
const test=(name,run)=>nodeTest(name,{skip:!evidenceAvailable},run);
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,copyFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {loadValidationResults} from '../server/validation-results.mjs';
const module=await import('../server/policy-validation.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const base=resolve('output/khu-platform-validation/policy-runtime-fix-20261017');
const result='evaluation-2026-10-17T13-56-48.004Z/result.json';
const load=async options=>{assert.equal(typeof module.loadPolicyValidation,'function','local comparison needs its own read-only evidence loader');return module.loadPolicyValidation(options);};

test('local comparison retains all nine trials and method medians without claiming policy superiority',async()=>{
 const r=await load();assert.equal(r.status,'verified');assert.equal(r.execution_verified,true);assert.equal(r.policy_effect,'not_demonstrated');
 assert.equal(r.requests,46224);assert.equal(r.errors,0);assert.equal(r.trials.length,9);assert.equal(r.rounds,3);assert.equal(r.slo_ms,50);
 assert.deepEqual(r.methods.map(m=>m.strategy),['fixed1','fixed2','adaptive']);
 assert.ok(Math.abs(r.methods[2].http_p95_ms-93.993)<1e-6);assert.ok(Math.abs(r.methods[2].slo_failure_percent-20.9696261682243)<1e-6);
 assert.equal(r.methods[1].slo_failure_percent,0);assert.ok(r.methods[1].http_p95_max_ms>50,'median zero does not mean every trial meets SLO');
 assert.equal(r.guard_checks,9);assert.equal(r.cloud_verified,false);assert.equal(r.model_accuracy_evaluated,false);
 assert.ok(r.trials.every(t=>t.flow_id.startsWith('flow-')&&t.cleanup_verified));assert.equal(r.read_only,true);
});

test('missing local evidence is unavailable and changed source bytes cannot show a verification pass',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'khu-policy-evidence-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 assert.equal((await load({directory})).status,'unavailable');
 await mkdir(dirname(join(directory,result)),{recursive:true});
 await copyFile(join(base,result),join(directory,result));await copyFile(join(base,'protocol.json'),join(directory,'protocol.json'));
 assert.equal((await load({directory})).status,'verified');
 const raw=JSON.parse(await readFile(join(directory,result),'utf8'));delete raw.trials[0].summary.errors;
 await writeFile(join(directory,result),JSON.stringify(raw));
 const r=await load({directory});assert.equal(r.status,'incomplete');assert.equal(r.execution_verified,false);assert.deepEqual(r.methods,[]);
});

test('cloud evidence and local comparison remain separate in the validation response',async()=>{
 const r=await loadValidationResults();assert.equal(r.results.length,3);assert.equal(r.results[2].automatic_vm_scaling,false);
 assert.equal(r.local_policy?.requests,46224);assert.equal(r.local_policy?.cloud_verified,false);
});
