import test from 'node:test';import assert from 'node:assert/strict';
import {setupCloudCampaignUI} from '../cloud-campaign-ui.mjs';
import {uiDocument} from './fixtures/ui-document.mjs';
test('campaign list refresh replaces a stale current record and labels provider counts with their recorded time',async()=>{
 const document=uiDocument(['cloud-campaign-panel','campaign-state','campaign-count','campaign-cost','campaign-children','campaign-select','campaign-cost-check','campaign-error','campaign-flow-one','campaign-flow-two',...['approve','start','expand','shrink','stop','refresh','review'].map(a=>'campaign-'+a)]);
 let record={run_id:'run-campaign-ui',created_at:'2026-10-17T00:00:00Z',phase:'DUAL_RUNNING',state:'ACTIVE',review:{max_compute_usd:.4608},provider_snapshot:{verified:true,running_vm_count:2,checked_at:'2026-10-17T00:01:00Z',children:[]}};
 const controller=setupCloudCampaignUI({document,api:async()=>({campaigns:[record]})});
 try{
  assert.equal(typeof controller.refresh,'function','manual and timer refresh must share fresh selection');await controller.refresh();assert.match(document.elements['campaign-count'].textContent,/2대/);
  record={...record,state:'CLEANED',phase:'CLEANED',connection_audit:{verified:true},provider_snapshot:{...record.provider_snapshot,running_vm_count:0}};
  await controller.refresh();assert.match(document.elements['campaign-state'].textContent,/정리 완료/);assert.match(document.elements['campaign-count'].textContent,/0대/);assert.match(document.elements['campaign-count'].textContent,/마지막 AWS 조회/);assert.equal(document.elements['campaign-expand'].disabled,true);
 }finally{controller.dispose();}
});
