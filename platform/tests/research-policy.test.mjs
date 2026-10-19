import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createPlatformServer} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {catalog,snapshots,clients,input} from './fixtures.mjs';
import * as panels from '../panels.mjs';

test('original policy comparisons are durably bound to the flow and selection version',async t=>{
  const store=createWorkflowStore(await mkdtemp(join(tmpdir(),'khu-research-')));
  const server=createPlatformServer({store,catalog,snapshots,clients:clients({researchCases:async()=>({cases:[{case_id:'original',phase:'planning'}]}),researchReplay:async()=>({case_id:'original',phase:'planning',execution_performed:false,proposal:{action:'DEPLOY'},oracle:{action:'DEPLOY'},agreement:true})})});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const base=`http://127.0.0.1:${server.address().port}/api/v1`;
  const post=async(path,body)=>{const r=await fetch(base+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};};
  let r=await post('/flows',input());const id=r.data.flow_id;
  const options=await fetch(base+'/research-policy/cases');assert.equal(options.status,200);
  r=await post(`/flows/${id}/research-policy`,{case_id:'original',phase:'planning',selection_version:0});
  assert.equal(r.status,200);assert.equal(r.data.research_validations[0].result.agreement,true);
  assert.equal((await store.read(id)).research_validations[0].selection_version,0);
  assert.equal(r.data.geon,undefined);assert.equal(r.data.execution,undefined);
  r=await post(`/flows/${id}/research-policy`,{case_id:'original',phase:'planning',selection_version:4});
  assert.equal(r.status,409);assert.equal(r.data.error.code,'stale_selection');
});

test('research panel keeps current GEON judgement distinct from original holdout case',()=>{
  assert.equal(typeof panels.researchPanelModel,'function');
  const r={selection_version:1,geon:{shadow_policy_assessments:[{phase:'planning',proposed_action:'ABSTAIN',rule_action:'DEPLOY',dataset_sha256:'hash'}]},research_validations:[{selection_version:1,phase:'planning',result:{case_id:'c',model_id:'TinyLlama/test',split:'test',proposal:{action:'DEPLOY'},oracle:{action:'DEPLOY'},agreement:true,transition_evidence:'direct_static_measurement'}}]};
  const view=panels.researchPanelModel(r,'planning');
  assert.equal(view.currentProposal,'규칙 판단 유지');assert.equal(view.caseProposal,'배포 가능');assert.equal(view.agreement,'일치');
  r.selection_version=2;assert.equal(panels.researchPanelModel(r,'planning').caseProposal,'—');
});
