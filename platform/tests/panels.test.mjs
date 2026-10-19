import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const panels=await import('../panels.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
test('measurement evidence is removed while monitoring and VM selection remain available',async()=>{
  const html=await readFile(new URL('../index.html',import.meta.url),'utf8');
  assert.doesNotMatch(html,/<section\b[^>]*id="evidence"/);
  assert.doesNotMatch(html,/href="#evidence"/);
  assert.match(html,/<section\b[^>]*id="infrastructure"/);
  assert.match(html,/<h2>모니터링 · 운영 판단<\/h2>/);
});
test('campaign evidence is available without a current flow and VM raw bytes reconcile',async()=>{
  assert.equal(typeof panels.campaignPanelModel,'function');
  const report=JSON.parse(await readFile(new URL('../data/khu-campaign-evidence.json',import.meta.url),'utf8'));
  const model=panels.campaignPanelModel(report);
  assert.equal(model.runCount,11);assert.equal(model.executionCount,2278);
  assert.equal(model.rawMiB,1025.61);assert.equal(model.vms.length,5);
  assert.equal(model.vms.reduce((s,v)=>s+v.rawBytes,0),report.totals.canonical_raw_bytes);
});
test('monitoring keeps visible metric fields before an app is measured',()=>{
  assert.equal(typeof panels.monitoringPanelModel,'function');
  const model=panels.monitoringPanelModel(null);
  assert.equal(model.metrics.length,6);assert.ok(model.metrics.every(m=>m.value==='—'));
  assert.equal(model.decision,'실측 대기');assert.match(model.note,/어플리케이션 배포/);
});
test('monitoring explains a created VM awaiting SSM without fabricating app measurements',()=>{
 const r={input:{execution_target:'cloud'},cloud_job_requested:true,cloud:{state:'CREATED',resource:{provider_instance_id:'i-owned'},ssm:{online:false},preparation:{stage:'GUEST_GUARD'}}};
 const v=panels.monitoringPanelModel(r);assert.match(v.status,/VM.*연결.*준비/);assert.match(v.note,/실행 버튼을 다시 누르지/);assert.equal(v.preparation.next,'#deployment');assert.ok(v.metrics.every(m=>m.value==='—'));
});
test('monitoring distinguishes model installation from creation and application RUNNING',()=>{
 const r={input:{execution_target:'cloud'},cloud_job_requested:true,cloud:{state:'PREPARING',preparation:{stage:'BOOTSTRAP_STATUS'}}};
 const waiting=panels.monitoringPanelModel(r);assert.match(waiting.status,/모델.*응용.*준비/);assert.match(waiting.decision,/준비/);
 r.cloud.state='RUNNING';r.execution={status:'RUNNING'};const ready=panels.monitoringPanelModel(r);assert.equal(ready.preparation,null);assert.equal(ready.status,'실행 중');assert.equal(ready.decision,'실측 대기');
});
test('monitoring shows a failed preparation and cleanup rather than a never-ending measurement wait',()=>{
 const r={input:{execution_target:'cloud'},cloud:{state:'CLEANED',failure:{stage:'BOOTSTRAP_START'},cleanup:{confirmed:true}}};
 const v=panels.monitoringPanelModel(r);assert.match(v.status,/배포 준비 실패/);assert.match(v.note,/배포 실행/);assert.equal(v.preparation.next,'#deployment');
});
test('cleanup during preparation is shown as an interruption with no fabricated execution',()=>{
 const r={input:{execution_target:'cloud'},cloud:{state:'CLEANED',failure:{stage:'APP_REGISTRATION',code:'cloud_cleanup_in_progress'},cleanup:{confirmed:true}}};
 const before=structuredClone(r),v=panels.monitoringPanelModel(r);
 assert.match(v.status,/준비 중단/);assert.doesNotMatch(v.status,/실패/);assert.match(v.note,/종료 요청/);assert.deepEqual(r,before);
 assert.ok(v.metrics.every(m=>m.value==='—'));
});
test('monitoring distinguishes stored app measurements from demo observations',()=>{
  const record={flow_id:'actual',input:{application:{app_id:'example'}},execution:{status:'STOPPED'},measurement:{sample_count:64,metrics:{resource:{cpu_average_percent:18,memory_peak_mib:260},inference:{latency_p95_ms:4.65,throughput_rps:250,error_rate_percent:0}}},feedback:{geon:{scaling_decision:{action:'KEEP',current_replicas:1,desired_replicas:1}}}};
  const before=JSON.stringify(record),model=panels.monitoringPanelModel(record);
  assert.equal(model.source,'실제 어플리케이션 실측');assert.equal(model.metrics[0].value,'18 %');
  assert.equal(model.metrics[2].value,'4.65 ms');assert.equal(model.decision,'유지');
  assert.equal(model.replicas,'1 → 1');assert.equal(model.status,'실행 종료');assert.equal(JSON.stringify(record),before);
  assert.equal(panels.monitoringPanelModel({feedback:{observation:{source:'demo_input',metrics:record.measurement.metrics}}}).source,'데모 관측 입력');
});
test('saved monitoring choices exclude unmeasured flows and order results by observation time',()=>{
  const choices=panels.monitoringRecords([
    {flow_id:'new-empty',created_at:'2026-10-01T13:00:00Z'},
    {flow_id:'old',measurement:{metrics:{},ended_at:'2026-10-01T10:00:00Z'}},
    {flow_id:'latest',measurement:{metrics:{},ended_at:'2026-10-01T11:00:00Z'}},
  ]);
  assert.deepEqual(choices.map(r=>r.flow_id),['latest','old']);
});
test('operation source follows the latest feedback while the HTTP probe remains separate',()=>{
  const model=panels.monitoringPanelModel({measurement:{source:'local_probe',sample_count:64,metrics:{inference:{latency_p95_ms:8.79}}},feedback:{observation:{source:'prometheus',metrics:{inference:{latency_p95_ms:4.94}}},geon:{scaling_decision:{action:'KEEP',current_replicas:1,desired_replicas:1}}}});
  assert.equal(model.source,'실제 어플리케이션 실측');assert.equal(model.decisionSource,'Prometheus 수집');
  assert.equal(model.metrics[2].value,'8.79 ms');
});
test('Prometheus-only measurements use server histogram and window labels',()=>{
  const model=panels.monitoringPanelModel({feedback:{observation:{source:'prometheus',sample_count:64,metrics:{inference:{latency_p95_ms:4.94,throughput_rps:1.07}}}}});
  assert.equal(model.metrics[2].label,'어플리케이션 처리 p95');assert.equal(model.metrics[3].label,'처리량 · 수집 구간');
  assert.equal(model.metrics[5].label,'관측 요청 수');assert.equal(model.source,'Prometheus 수집');
});

test('a Prometheus observation saved by the replica measurement route keeps its actual source label',()=>{
 const v=panels.monitoringPanelModel({measurement:{source:'prometheus',sample_count:64,metrics:{inference:{latency_p95_ms:5}}}});
 assert.equal(v.source,'Prometheus 수집');assert.equal(v.metrics[2].label,'어플리케이션 처리 p95');
});

test('monitoring labels measurements from before an applied membership change and requests a new measurement',()=>{
 const r={flow_id:'flow-loop',execution:{status:'RUNNING'},replica_group:{group_id:'group-loop',membership_epoch:2,applied_revision:2},measurement:{source:'local_probe',group_id:'group-loop',membership_epoch:1,metrics:{inference:{latency_p95_ms:80}}}};
 const v=panels.monitoringPanelModel(r);assert.equal(v.needsRemeasure,true);assert.match(v.source,/변경 전/);assert.match(v.note,/다시 측정/);assert.equal(v.metrics[2].value,'80 ms');
 r.measurement.membership_epoch=2;assert.equal(panels.monitoringPanelModel(r).needsRemeasure,false);
});
