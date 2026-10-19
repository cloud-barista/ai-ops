import assert from 'node:assert/strict';
import test from 'node:test';
const views=await import('../view-model.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
test('cards use neutral missing measurements and clear units',()=>{assert.equal(typeof views.candidateCardModel,'function');const c={display_name:'CPU 8',name:'m6a.2xlarge',shape:{cpu_cores:8,memory_mib:32768,gpu_count:0},price:{hourly_usd:null,work_usd:null},measurement:null,reason:'조건 충족',origin:'demo_input'};const v=views.candidateCardModel(c);assert.equal(v.latency,'비교 측정 없음');assert.equal(v.cost,'가격 확인 필요');assert.equal(v.source,'데모 자원');c.measurement={batch_p95_ms:3,throughput:800};assert.match(views.candidateCardModel(c).latency,/배치 p95.*ms/);});
test('four steps preserve mock provenance and RUNNING state',()=>{assert.equal(typeof views.flowViewModel,'function');const v=views.flowViewModel({state:'MOCK_RUNNING',execution:{status:'RUNNING',mode:'mock'},observation_origin:'demo_input',analysis:{mode:'structured'}});assert.equal(v.step,4);assert.equal(v.observation,'데모 입력');assert.match(v.status,/Mock/);assert.ok(!v.status.includes('완료'));});
test('selection invalidation returns user to planning step',()=>{assert.equal(typeof views.flowViewModel,'function');assert.equal(views.flowViewModel({state:'SELECTED',selection_version:2}).step,3);assert.equal(views.flowViewModel({state:'ANALYZED'}).step,2);});
test('actual execution status remains visible for running and stopped apps',()=>{
  for(const status of ['RUNNING','STOPPED']){const v=views.flowViewModel({state:'LOCAL_'+status,execution:{mode:'local_process',status},observation_origin:'observed'});assert.equal(v.status,'실제 어플리케이션 '+status);assert.equal(v.execution,'실제 로컬 어플리케이션 · '+status);}
});

test('real execution replaces the old plan approval in the status bar',()=>{
  for(const mode of ['local_process','cloud_vm'])for(const status of ['RUNNING','STOPPED','UNKNOWN']){
    const r={state:'DEPLOY_APPROVED',execution:{mode,status}};
    assert.equal(views.flowViewModel(r).status,'실제 어플리케이션 '+status);
  }
  const stopped={state:'FEEDBACK_RECORDED',execution:{mode:'local_process',status:'STOPPED'}};
  assert.equal(views.flowViewModel(stopped).status,'실제 어플리케이션 STOPPED');
  const running={state:'FEEDBACK_RECORDED',execution:{mode:'local_process',status:'RUNNING'}};
  assert.equal(views.flowViewModel(running).status,'운영 피드백 판단 기록');
});

test('a failed bootstrap is displayed as undeployed even when an old receipt has a synthetic STOPPED execution',()=>{
 const r={state:'DEPLOY_APPROVED',input:{execution_target:'cloud'},cloud:{state:'CLEANED',cleanup:{confirmed:true},failure:{stage:'BOOTSTRAP_START'}},execution:{mode:'cloud_vm',source:'provider_termination',status:'STOPPED'}};
 const v=views.flowViewModel(r);assert.match(v.status,/배포 준비 실패/);assert.equal(v.execution,'어플리케이션 배포 미완료');
});
test('edited application conditions require fresh analysis before using the old plan',()=>{const v=views.flowViewModel({state:'DEPLOY_APPROVED',input_dirty:true});assert.equal(v.step,1);assert.equal(v.needsAnalysis,true);assert.match(v.status,/다시 분석/);});
test('keyboard focus returns to the selected candidate after controls are rebuilt',()=>{
  assert.equal(typeof views.captureSelectionFocus,'function');assert.equal(typeof views.restoreSelectionFocus,'function');
  const document={activeElement:{dataset:{candidateControl:'radio',candidateId:'demo-cpu8'}}};
  const token=views.captureSelectionFocus(document);const replacement={dataset:{candidateControl:'radio',candidateId:'demo-cpu8'},disabled:false,focus(){document.activeElement=this;}};
  document.activeElement={};document.querySelectorAll=()=>[replacement];views.restoreSelectionFocus(document,token);assert.equal(document.activeElement,replacement);
  assert.equal(views.captureSelectionFocus({activeElement:{dataset:{}}}),null);
});
test('local evidence displays the actual app measurement separately from historical benchmarks',()=>{
  assert.equal(typeof views.measurementEvidenceModel,'function');
  const measured={sample_count:64,metrics:{inference:{latency_p95_ms:4.65,throughput_rps:257.59},resource:{memory_peak_mib:260.04}}};
  const v=views.measurementEvidenceModel({input:{execution_target:'local'},measurement:measured},{measurement:null});
  assert.equal(v.title,'이 Flow의 실제 어플리케이션 실측');
  assert.equal(v.metrics[0].value,'4.65 ms');
  assert.equal(v.metrics[1].value,'257.59 요청/초');
  assert.equal(v.metrics[2].value,'64 요청');
  assert.equal(v.raw,measured);
});
test('historical evidence keeps batch and input throughput units',()=>{
  const measured={batch_p95_ms:3,throughput:800};
  const v=views.measurementEvidenceModel({input:{execution_target:'historical'}},{measurement:measured});
  assert.equal(v.title,'선택한 VM의 과거 벤치마크');
  assert.equal(v.metrics[0].value,'3 ms');
  assert.equal(v.metrics[1].value,'800 입력/초');
  assert.equal(v.raw,measured);
});
test('evidence gives the next action before measurement without inventing results',()=>{
  const v=views.measurementEvidenceModel({input:{execution_target:'local'}},null);
  assert.deepEqual(v.metrics,[]);
  assert.match(v.note,/어플리케이션 배포.*실측/);
});

test('existing saved shortlists still display all CPU and GPU shapes in their original recommendation order',()=>{
  assert.equal(typeof views.infrastructureCandidates,'function');
  const all=['cpu2','cpu8','cpu32','gpu8','gpu32'].map((id,index)=>({candidate_id:id,name:id,selectable:true,shape:{gpu_count:index>2?1:0}}));
  const receipt={candidates:{all,primary:[all[1],all[0],all[2]]}};
  const before=structuredClone(receipt);
  assert.deepEqual(views.infrastructureCandidates(receipt).map(c=>c.candidate_id),['cpu8','cpu2','cpu32','gpu8','gpu32']);
  assert.deepEqual(receipt,before);
});
test('displayed excluded shapes remain disabled and actual local targets do not borrow historical GPUs',()=>{
  assert.equal(typeof views.infrastructureCandidates,'function');
  const gpu={candidate_id:'gpu8',selectable:true},cpu={candidate_id:'cpu2',selectable:false};
  assert.deepEqual(views.infrastructureCandidates({candidates:{all:[cpu,gpu],primary:[gpu]}}),[gpu,cpu]);
  const local={candidate_id:'khu-local-pc',selectable:true};
  assert.deepEqual(views.infrastructureCandidates({input:{execution_target:'local'},candidates:{all:[local],primary:[local]}}),[local]);
});
test('a historical shape with no deployment observation is not labelled ready to select',()=>{
  const c={display_name:'GPU',shape:{cpu_cores:8,memory_mib:32768,gpu_count:1},price:{},origin:'historical_shape',feasible:true,selectable:false};
  assert.equal(views.candidateCardModel(c).fit,'배포 정보 확인 필요');
});
