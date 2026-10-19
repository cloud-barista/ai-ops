import nodeTest from 'node:test';
import {existsSync} from 'node:fs';
// Optional historical-evidence integration checks require the original files.
const evidenceAvailable=existsSync(new URL('../../output/khu-platform-validation/policy-runtime-fix-20261017/evaluation-2026-10-17T13-56-48.004Z/result.json',import.meta.url));
const test=(name,run)=>nodeTest(name,{skip:!evidenceAvailable},run);
import assert from 'node:assert/strict';
import {uiDocument,descendants} from './fixtures/ui-document.mjs';
import {setupValidationUI} from '../validation-ui.mjs';
import {loadValidationResults} from '../server/validation-results.mjs';
const ids=['validation-overview','validation-cards','validation-runtime','validation-loaded-at','validation-status','validation-reload','policy-validation','policy-validation-overview'];

test('validation refresh shows local execution evidence, all trials, and the negative policy conclusion separately from AWS',async()=>{
 const document=uiDocument(ids),data=await loadValidationResults(),calls=[];
 const c=setupValidationUI({document,api:async p=>{calls.push(p);return p==='/validation-results'?data:{enabled:false,pending_cleanup:[]};}});await c.ready;
 const box=document.elements['policy-validation'],text=box.textContent;
 assert.match(text,/46,224/);assert.match(text,/성능 개선은 확인하지 못했습니다/);assert.match(text,/각 방식 3회/);assert.match(text,/중앙값/);
 assert.match(text,/VM 수/);assert.match(text,/실제 클라우드 비용/);assert.match(text,/50ms/);
 const rows=descendants(box).filter(e=>e.tagName==='TR');assert.equal(rows.length,14,'two table headers, three method rows, nine trial rows');
 assert.ok(descendants(box).some(e=>e.tagName==='A'&&/\?flow=flow-[\w-]+#events/.test(e.href)));
 assert.match(document.elements['policy-validation-overview'].textContent,/로컬/);assert.match(document.elements['validation-cards'].textContent,/CPU VM 배포/);
 assert.ok(calls.every(p=>p==='/validation-results'||p==='/cloud/readiness'));
});

test('a failed reload clears old local success and never renders missing evidence as zero errors',async()=>{
 const document=uiDocument(ids);let fail=false;const data=await loadValidationResults();
 const c=setupValidationUI({document,api:async p=>{if(p==='/cloud/readiness')return {enabled:false,pending_cleanup:[]};if(fail)throw Error('offline');return data;}});await c.ready;
 assert.match(document.elements['policy-validation'].textContent,/46,224/);fail=true;await c.refresh();
 assert.doesNotMatch(document.elements['policy-validation'].textContent,/46,224|오류 0건|실행 연속성 확인/);
 assert.match(document.elements['policy-validation'].textContent,/불러올 수 없습니다/);
});
