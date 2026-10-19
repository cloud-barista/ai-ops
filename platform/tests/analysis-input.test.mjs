import assert from 'node:assert/strict';
import test from 'node:test';
const mod=await import('../analysis-input.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const spec={app_id:'classifier',app_version:'1.0.0',cpu_cores:2,memory_mib:2048,artifact:{type:'script',uri:'demo://classifier'}};
test('natural-language form sends metadata and text without overriding it with numeric fields',()=>{
  assert.equal(typeof mod.buildAnalysisInput,'function');
  assert.deepEqual(mod.buildAnalysisInput({mode:'natural_language',request:' CPU classification ',appSpec:spec}),{input_type:'natural_language',candidate_id:'qwen3.5-profile-generator',request:'CPU classification',application:{app_id:'classifier',app_version:'1.0.0',artifact:{type:'script',uri:'demo://classifier'}}});
});
test('structured form preserves the deterministic AppSpec path',()=>{
  assert.equal(typeof mod.buildAnalysisInput,'function');assert.deepEqual(mod.buildAnalysisInput({mode:'structured',appSpec:spec}),{input_type:'structured',app_spec:spec});
});
test('a chosen Llama candidate is passed to the actual profile generator',()=>{
  const result=mod.buildAnalysisInput({mode:'natural_language',request:'CPU classification',appSpec:spec,candidateId:'llama3.2-profile-generator'});
  assert.equal(result.candidate_id,'llama3.2-profile-generator');
  assert.equal(result.application.app_id,'classifier');assert.equal(result.app_spec,undefined);
});
test('unrecognized LLM candidates are rejected before preparing a generation request',()=>{
  assert.throws(()=>mod.buildAnalysisInput({mode:'natural_language',request:'CPU classification',appSpec:spec,candidateId:'remote-model'}),/invalid_llm_candidate/);
});

test('an empty historical cohort cannot block local or cloud analysis',()=>{
  assert.equal(typeof mod.analysisControlDisabled,'function');
  for(const target of ['local','cloud']){
    for(const id of ['cohort','objective','throughput','latency'])
      assert.equal(mod.analysisControlDisabled(id,{target,mode:'structured'}),true);
    assert.equal(mod.analysisControlDisabled('local-model',{target,mode:'structured'}),false);
    assert.equal(mod.analysisControlDisabled('cpu',{target,mode:'structured'}),false);
  }
});

test('historical analysis validates benchmark fields without the inactive local model',()=>{
  assert.equal(typeof mod.analysisControlDisabled,'function');
  for(const id of ['cohort','throughput','latency'])
    assert.equal(mod.analysisControlDisabled(id,{target:'historical',mode:'structured'}),false);
  assert.equal(mod.analysisControlDisabled('local-model',{target:'historical',mode:'structured'}),true);
});

test('natural-language analysis excludes structured inputs while allowing a new review during execution',()=>{
  assert.equal(typeof mod.analysisControlDisabled,'function');
  const natural={target:'local',mode:'natural_language'};
  assert.equal(mod.analysisControlDisabled('natural-request',natural),false);
  assert.equal(mod.analysisControlDisabled('cpu',natural),true);
  assert.equal(mod.analysisControlDisabled('profile-llm',{target:'local',mode:'structured'}),true);
  assert.equal(mod.analysisControlDisabled('natural-request',{target:'local',mode:'structured'}),true);
  assert.equal(mod.analysisControlDisabled('app-id',{...natural,busy:true}),true);
  assert.equal(mod.analysisControlDisabled('local-model',{...natural,live:true}),false);
});
test('a running experiment does not lock draft requirements or the LLM selector',()=>{
 const state={target:'cloud',mode:'natural_language',live:true};
 assert.equal(mod.analysisControlDisabled('natural-request',state),false);
 assert.equal(mod.analysisControlDisabled('profile-llm',state),false);
 assert.equal(mod.analysisControlDisabled('new-flow',state),false);
 assert.equal(mod.analysisControlDisabled('new-flow',{...state,busy:true}),true);
});
test('selecting another resource during execution reviews a new flow and never changes the running binding',async()=>{
 assert.equal(typeof mod.reviewRunningCandidate,'function');
 const r={flow_id:'flow-live',selection_version:1,selected_candidate_id:'old',requested_application:{app_id:'app',app_version:'1.0.0'},input:{application:{app_id:'app',app_version:'1.0.0-poc.old'},execution_target:'cloud',analysis_input:{input_type:'structured',app_spec:{app_id:'app',app_version:'1.0.0-poc.old',cpu_cores:2}}},candidates:{all:[{candidate_id:'new',name:'m6a.large',selectable:true}]}};
 const before=structuredClone(r),calls=[];
 const result=await mod.reviewRunningCandidate(r,'new',async(path,body)=>{calls.push({path,body});return path==='/flows'?{flow_id:'flow-review',selection_version:0,candidates:{all:[{candidate_id:'new-v2',name:'m6a.large',selectable:true}]}}:{flow_id:'flow-review',selected_candidate_id:'new-v2'};});
 assert.deepEqual(r,before);assert.equal(result.flow_id,'flow-review');assert.equal(calls[0].body.execution_target,'cloud');assert.equal(calls[0].body.analysis_input.app_spec.app_version,'1.0.0');assert.equal(calls[1].path,'/flows/flow-review/selection');assert.equal(calls[1].body.candidate_id,'new-v2');
});
test('one shared execution lock covers cloud preparation and running apps, and releases only settled runs',()=>{
 assert.equal(typeof mod.analysisExecutionActive,'function');
 assert.equal(mod.analysisExecutionActive(null),false);
 assert.equal(mod.analysisExecutionActive({input:{execution_target:'cloud'},cloud_job_requested:true,cloud:{state:'READY'}}),true);
 assert.equal(mod.analysisExecutionActive({input:{execution_target:'cloud'},cloud_job_requested:false,cloud:{state:'RUNNING'}}),true);
 assert.equal(mod.analysisExecutionActive({input:{execution_target:'cloud'},cloud_job_requested:false,cloud:{state:'CLEANED'},appdeploy:{status:'STOPPED'},execution:{status:'STOPPED'}}),false);
 assert.equal(mod.analysisExecutionActive({input:{execution_target:'local'},appdeploy:{status:'RUNNING'},execution:{status:'RUNNING'}}),true);
});

test('local execution cannot request an unsupported GPU from the structured form',()=>{
  assert.equal(typeof mod.requirementExecutionLimits,'function');
  assert.deepEqual(mod.requirementExecutionLimits({target:'local',scenario:'gpu'}),{
    scenario:'cpu',gpuAvailable:false,maxReplicas:2,fixedReplicas:false,
  });
});

test('AWS GPU shows the same single replica limit that execution supports',()=>{
  assert.equal(typeof mod.requirementExecutionLimits,'function');
  assert.deepEqual(mod.requirementExecutionLimits({target:'cloud',scenario:'gpu'}),{
    scenario:'gpu',gpuAvailable:true,maxReplicas:1,fixedReplicas:true,
  });
});

test('CPU execution and historical comparison retain their adjustable replica limit',()=>{
  assert.equal(typeof mod.requirementExecutionLimits,'function');
  for(const target of ['local','cloud','historical']){
    assert.deepEqual(mod.requirementExecutionLimits({target,scenario:'cpu'}),{
      scenario:'cpu',gpuAvailable:target!=='local',maxReplicas:2,fixedReplicas:false,
    });
  }
  assert.equal(mod.requirementExecutionLimits({target:'historical',scenario:'gpu'}).maxReplicas,2);
});
