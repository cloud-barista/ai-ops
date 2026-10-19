import test from 'node:test';
import assert from 'node:assert/strict';
const ui=await import('../prometheus-ui.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
test('live collection and archived collection retain their actual source and metric units',()=>{
  assert.equal(typeof ui.prometheusPanelModel,'function');
  const observation={source:'prometheus',sample_count:64,ended_at:'2026-10-05T01:00:00Z',metrics:{resource:{cpu_average_percent:2,memory_peak_mib:260},inference:{latency_p95_ms:9,throughput_rps:6.4,error_rate_percent:0}}};
  const live=ui.prometheusPanelModel({state:'COLLECTING',server_ready:true,can_feedback:true,observation,metrics:observation.metrics});
  assert.equal(live.metrics[2].label,'어플리케이션 처리 p95');assert.equal(live.metrics[2].value,'9 ms');assert.equal(live.canFeedback,true);
  const saved=ui.prometheusPanelModel({state:'IDLE',server_ready:true}, {prometheus_measurement:observation});assert.match(saved.status,/저장된/);assert.equal(saved.canFeedback,false);assert.equal(saved.metrics[5].value,'64 요청');
});
test('missing samples remain blank and cannot be submitted as a successful observation',()=>{
  assert.equal(typeof ui.prometheusPanelModel,'function');
  const view=ui.prometheusPanelModel({state:'WAITING_REQUESTS',server_ready:true,metrics:{resource:{cpu_average_percent:0,memory_peak_mib:250}}});
  assert.equal(view.canFeedback,false);assert.equal(view.metrics[0].value,'0 %');assert.equal(view.metrics[2].value,'—');assert.equal(view.metrics[5].value,'—');
});

test('stopped replica-group receipt reopens its bound Prometheus measurement without enabling feedback',()=>{
  const observation={source:'prometheus',flow_id:'flow-saved',group_id:'group-saved',membership_epoch:2,sample_count:64,ended_at:'2026-10-05T11:33:45Z',metrics:{resource:{cpu_average_percent:.17,memory_peak_mib:521.7},inference:{latency_p95_ms:4.67,throughput_rps:1.07,error_rate_percent:0}}};
  const r={flow_id:'flow-saved',execution:{status:'STOPPED',mode:'cloud_vm'},replica_group:{group_id:'group-saved',membership_epoch:3},measurement:observation};
  const saved=ui.prometheusPanelModel({state:'IDLE',server_ready:true},r);
  assert.match(saved.status,/저장된/);assert.equal(saved.archived,true);assert.equal(saved.canFeedback,false);assert.equal(saved.metrics[2].value,'4.67 ms');assert.equal(saved.metrics[5].value,'64 요청');
  const unrelated=ui.prometheusPanelModel({state:'IDLE'}, {...r,flow_id:'flow-another'});
  assert.equal(unrelated.metrics[2].value,'—');assert.equal(unrelated.canFeedback,false);
});

test('cloud app monitoring refresh calls the actual same-Flow collection endpoint',async t=>{
  t.mock.method(globalThis,'setInterval',()=>1);
  class Element { constructor(){this.listeners={};}append(){}replaceChildren(){}setAttribute(){}addEventListener(name,handler){this.listeners[name]=handler;} }
  const elements=new Map(),doc={hidden:false,body:{dataset:{workspaceView:'operation'}},getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element(),createElementNS:()=>new Element()};
  const r={flow_id:'flow-cloud-live',input:{execution_target:'cloud'},execution:{mode:'cloud_vm',status:'RUNNING',execution_id:'group-cloud'}},calls=[];
  const monitoring=ui.setupPrometheusMonitoring({document:doc,getReceipt:()=>r,isBusy:()=>false,onFeedback:async()=>{},api:async path=>{calls.push(path);return {flow_id:r.flow_id,state:'WAITING_REQUESTS',server_ready:true};}});
  await monitoring.refresh();assert.deepEqual(calls,['/flows/flow-cloud-live/prometheus']);
});

test('collection continues while a newer approved revision must be applied before another judgement',async t=>{
  t.mock.method(globalThis,'setInterval',()=>1);
  class Element {constructor(){this.listeners={};}append(){}replaceChildren(){}setAttribute(){}addEventListener(name,handler){this.listeners[name]=handler;}}
  const elements=new Map(),doc={hidden:false,body:{dataset:{workspaceView:'operation'}},getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element(),createElementNS:()=>new Element()};
  const r={flow_id:'flow-pending',input:{execution_target:'local'},execution:{status:'RUNNING',execution_id:'group-pending'},replica_group:{applied_revision:1,operation_state:'IDLE'},feedback:{geon:{manifest_revisions:[{revision:2}]}}};let calls=0,judgements=0;
  const monitoring=ui.setupPrometheusMonitoring({document:doc,getReceipt:()=>r,isBusy:()=>false,onFeedback:async()=>{judgements++;},api:async()=>{calls++;return {flow_id:r.flow_id,state:'COLLECTING',can_feedback:true,server_ready:true};}});
  await monitoring.refresh();assert.equal(calls,1);assert.equal(elements.get('prometheus-refresh').disabled,false);assert.equal(elements.get('prometheus-feedback').disabled,true);
  await elements.get('prometheus-feedback').listeners.click();assert.equal(judgements,0);
  r.replica_group.applied_revision=2;
  for(const state of ['PENDING','UNKNOWN']){r.feedback_state=state;monitoring.render();assert.equal(elements.get('prometheus-feedback').disabled,true);await elements.get('prometheus-feedback').listeners.click();assert.equal(judgements,0);}
  r.feedback_state='RECORDED';monitoring.render();assert.equal(elements.get('prometheus-feedback').disabled,false);
  await elements.get('prometheus-feedback').listeners.click();assert.equal(judgements,1);
});
