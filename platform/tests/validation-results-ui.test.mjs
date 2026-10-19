import nodeTest from 'node:test';
import {existsSync} from 'node:fs';
// Optional historical-evidence integration checks require the original files.
const evidenceAvailable=['cpu-result-2026-10-16T08-59-14-944Z.json','gpu-result-2026-10-16T16-22-47-089Z.json','campaign-result-2026-10-16T17-15-32-712Z.json'].every(f=>existsSync(new URL('../../output/khu-platform-validation/cloud-live-validation/'+f,import.meta.url)));
const test=(name,run)=>nodeTest(name,{skip:!evidenceAvailable&&!name.startsWith('unavailable evidence')},run);
import assert from 'node:assert/strict';
import {uiDocument,descendants} from './fixtures/ui-document.mjs';
import {loadValidationResults} from '../server/validation-results.mjs';
const ui=await import('../validation-ui.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const fixture=()=>uiDocument(['validation-overview','validation-cards','validation-runtime','validation-loaded-at','validation-status','validation-reload']);
test('result screen shows historical CPU/GPU success, four measured VM windows, and working saved Flow links',async()=>{
 assert.equal(typeof ui.setupValidationUI,'function');const document=fixture(),calls=[];
 const data=await loadValidationResults({readFlow:async id=>({flow_id:id,execution:{status:'STOPPED',actual_replicas:0},cloud:{state:'CLEANED'}})});
 const controller=ui.setupValidationUI({document,api:async path=>{calls.push(path);if(path==='/validation-results')return data;if(path==='/cloud/readiness')return {enabled:false,pending_cleanup:[]};throw Error('unexpected request');}});
 await controller.ready;
 const text=document.elements['validation-cards'].textContent;
 assert.match(text,/256/);assert.match(text,/cuda:0/);assert.match(text,/STOPPED/);assert.match(text,/HTTP p95/);assert.match(text,/모델 연산 p95/);
 assert.match(text,/자동 VM 증감/);assert.match(document.elements['validation-runtime'].textContent,/꺼짐/);
 const tree=descendants(document.elements['validation-cards']);
 const timeline=tree.find(n=>n.attributes['aria-label']==='저장된 AWS VM 수 변경 이력');
 assert.equal(timeline.children.length,4);assert.match(timeline.children[1].textContent,/2대/);
 const link=tree.find(n=>n.tagName==='A'&&n.textContent==='모니터링');assert.equal(link.href,'?flow=flow-54b255a3-1227-4226-a6bd-74d7341a67b5#operation');
 await document.elements['validation-reload'].dispatch('click');assert.equal(calls.filter(p=>p==='/validation-results').length,2);
 assert.ok(calls.every(p=>p==='/validation-results'||p==='/cloud/readiness'));
});
test('unavailable evidence or readiness produces an explicit unknown state instead of a green success',async()=>{
 assert.equal(typeof ui.setupValidationUI,'function');const document=fixture();
 const controller=ui.setupValidationUI({document,api:async p=>{if(p==='/cloud/readiness')throw Error('offline');return {results:[{id:'cpu',title:'CPU VM 배포',status:'unavailable',checks:[],flow_ids:[],current:[],windows:[],timeline:[]}]};}});
 await controller.ready;
 assert.match(document.elements['validation-cards'].textContent,/기록을 불러올 수 없음/);
 assert.match(document.elements['validation-runtime'].textContent,/확인할 수 없음/);
 assert.doesNotMatch(document.elements['validation-cards'].textContent,/검증 통과/);
});
