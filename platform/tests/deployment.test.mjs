import assert from 'node:assert/strict';
import test from 'node:test';
const deployment=await import('../deployment.mjs').catch(error=>{if(error.code==='ERR_MODULE_NOT_FOUND')return {};throw error;});
const approved=()=>({flow_id:'flow-deploy',selection_version:1,input:{execution_target:'local'},selected_candidate_id:'khu-local-pc',geon:{decision:{action:'DEPLOY'},guard:{status:'APPROVED'},deployment_plan:{selected_candidate_id:'khu-local-pc'}}});

test('deployment is discoverable before analysis and directs the user to the missing prerequisite',()=>{
  assert.equal(typeof deployment.deploymentViewModel,'function');
  const v=deployment.deploymentViewModel(null,{target:'local'});
  assert.equal(v.local,true);assert.equal(v.canDeploy,false);assert.equal(v.nextHref,'#application');
  assert.equal(v.status,'WAITING');
});
test('deploy is available from an approved local plan without a separate registration click',()=>{
  assert.equal(typeof deployment.deploymentViewModel,'function');
  const r=approved();assert.equal(deployment.deploymentViewModel(r).canDeploy,true);
  r.appdeploy={status:'PREPARED'};assert.equal(deployment.deploymentViewModel(r).canDeploy,true);
  assert.equal(deployment.deploymentViewModel(r,{inputDirty:true}).canDeploy,false);
  assert.equal(deployment.deploymentViewModel(r,{busy:true}).canDeploy,false);
});
test('historical candidates and unconfirmed or stopped deployments cannot launch a worker',()=>{
  assert.equal(typeof deployment.deploymentViewModel,'function');
  for(const change of [
    {input:{execution_target:'historical'}},
    {selected_candidate_id:'demo-cpu8'},
    {geon:{decision:{action:'DEPLOY'},guard:{status:'REJECTED'}}},
    {appdeploy:{status:'UNKNOWN'}},
    {execution:{status:'STOPPED'}},
  ])assert.equal(deployment.deploymentViewModel({...approved(),...change}).canDeploy,false);
  assert.equal(deployment.deploymentViewModel({...approved(),input:{execution_target:'historical'}}).local,false);
});
test('one-click deployment refuses an unapproved plan before any external action',async()=>{
  assert.equal(typeof deployment.deploySelectedApp,'function');
  await assert.rejects(()=>deployment.deploySelectedApp({...approved(),geon:null},()=>{throw Error('external action must not run');}),/approved_local_plan_required/);
});
test('only confirmed terminal deployments offer a fresh execution',()=>{
  assert.equal(deployment.deploymentViewModel({...approved(),execution:{status:'STOPPED'}}).canRestart,true);
  assert.equal(deployment.deploymentViewModel({...approved(),execution:{status:'RUNNING'}}).canRestart,false);
  assert.equal(deployment.deploymentViewModel({...approved(),appdeploy:{status:'UNKNOWN'}}).canRestart,false);
});

test('VM cleanup after bootstrap failure is distinguished from an application deployment',()=>{
 const v=deployment.deploymentViewModel({input:{execution_target:'cloud'},cloud:{state:'CLEANED',cleanup:{confirmed:true},failure:{stage:'BOOTSTRAP_START'}}});
 assert.match(v.title,/배포 준비 실패/);assert.match(v.description,/어플리케이션 배포.*완료되지/);assert.match(v.description,/VM.*정리/);
});

test('a selected resource without a plan points to the screen that can create the plan',()=>{
  for(const target of ['local','historical','cloud']){
    const r={...approved(),input:{execution_target:target},application_profile:{app_id:'classifier'},geon:null};
    const v=deployment.deploymentViewModel(r);
    assert.equal(v.canDeploy,false);
    assert.equal(v.nextHref,'#infrastructure',target+' must lead to the plan creation button');
  }
});

test('cloud deployment entered before analysis starts at requirements',()=>{
  assert.equal(deployment.deploymentViewModel(null,{target:'cloud'}).nextHref,'#application');
});

test('approved historical plans show the mock action instead of asking to enter requirements again',()=>{
  const r={...approved(),input:{execution_target:'historical'}};
  assert.equal(deployment.deploymentViewModel(r).nextHref,null);
  assert.equal(deployment.deploymentViewModel({...r,execution:{status:'RUNNING',adapter:'mock'}}).nextHref,'#operation');
  assert.equal(deployment.deploymentViewModel(r,{inputDirty:true}).nextHref,'#application');
});

test('rejected cloud and historical plans point to their judgement before execution',()=>{
  for(const target of ['cloud','historical']){
    const r={...approved(),input:{execution_target:target},geon:{decision:{action:'HOLD'},guard:{status:'REJECTED'}}};
    assert.equal(deployment.deploymentViewModel(r).nextHref,'#planning');
    assert.equal(deployment.deploymentViewModel(r).canDeploy,false);
  }
});
