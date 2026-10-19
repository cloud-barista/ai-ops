import test from 'node:test';
import assert from 'node:assert/strict';
import * as navigation from '../navigation.mjs';
import {uiDocument} from './fixtures/ui-document.mjs';

function fixture(){
  const document=uiDocument(['action-feedback','action-feedback-title','action-feedback-detail','action-feedback-close','trigger']);
  const listeners={};document.addEventListener=(name,callback)=>{listeners[name]=callback;};
  for(const element of Object.values(document.elements)){
    element.dataset={};element.getAttribute=name=>element.attributes[name]??null;
    element.removeAttribute=name=>{delete element.attributes[name];};element.closest=()=>element;
  }
  const button=document.elements.trigger;button.tagName='BUTTON';
  let clock=1000,next=0;const intervals=new Map(),timeouts=new Map();
  const timers={setInterval(fn){const id=++next;intervals.set(id,fn);return id;},clearInterval:id=>intervals.delete(id),setTimeout(fn,ms){const id=++next;timeouts.set(id,{fn,at:clock+ms});return id;},clearTimeout:id=>timeouts.delete(id)};
  const feedback=navigation.setupActionFeedback(document,{now:()=>clock,timers});
  const click=()=>listeners.click({target:button});
  const advance=ms=>{clock+=ms;for(const fn of intervals.values())fn();for(const [id,t] of [...timeouts])if(t.at<=clock){timeouts.delete(id);t.fn();}};
  return {document,feedback,button,click,advance,e:document.elements};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}

test('manual work visibly starts before a delayed response and shows elapsed time and completion',async()=>{
  assert.equal(typeof navigation.setupActionFeedback,'function','a visible shared action feedback controller is required');
  const f=fixture(),request=deferred();f.click();
  const pending=f.feedback.run('요구사항 분석 중…',()=>request.promise);
  assert.equal(f.e['action-feedback'].hidden,false);
  assert.equal(f.e['action-feedback'].dataset.state,'pending');
  assert.equal(f.e['action-feedback-title'].textContent,'요구사항 분석 중…');
  assert.equal(f.button.getAttribute('aria-busy'),'true');
  f.advance(7000);assert.match(f.e['action-feedback-detail'].textContent,/7초/);
  request.resolve({ok:true});assert.deepEqual(await pending,{ok:true});
  assert.equal(f.e['action-feedback'].dataset.state,'success');
  assert.match(f.e['action-feedback-title'].textContent,/완료/);
  assert.equal(f.button.getAttribute('aria-busy'),null);
  f.advance(6000);assert.equal(f.e['action-feedback'].hidden,true);
});

test('failed requests stay visible, release the trigger, and never look completed',async()=>{
  const f=fixture(),request=deferred();f.click();
  const pending=f.feedback.run('자원 조회 중…',()=>request.promise),error=Error('private diagnostics');
  request.reject(error);await assert.rejects(pending,e=>e===error);
  assert.equal(f.e['action-feedback'].dataset.state,'error');
  assert.match(f.e['action-feedback-title'].textContent,/실패/);
  assert.doesNotMatch(f.e['action-feedback-detail'].textContent,/private diagnostics/);
  assert.equal(f.button.getAttribute('aria-busy'),null);
  f.advance(60000);assert.equal(f.e['action-feedback'].hidden,false);
});

test('an older response and its completion timer cannot hide a newer pending request',async()=>{
  const f=fixture(),one=deferred(),two=deferred();
  const a=f.feedback.run('첫 조회 중…',()=>one.promise),b=f.feedback.run('새 조회 중…',()=>two.promise);
  one.resolve();await a;assert.equal(f.e['action-feedback-title'].textContent,'새 조회 중…');
  assert.equal(f.e['action-feedback'].dataset.state,'pending');
  two.resolve();await b;
  const third=deferred(),c=f.feedback.run('배포 중…',()=>third.promise);
  f.advance(10000);assert.equal(f.e['action-feedback'].hidden,false);assert.match(f.e['action-feedback-title'].textContent,/배포/);
  third.resolve();await c;
});

test('the same loading button cannot dispatch a duplicate request and existing disabled state is preserved',async()=>{
  const f=fixture(),request=deferred();let count=0;f.button.disabled=true;f.click();
  const one=f.feedback.run('실측 중…',()=>{count++;return request.promise;});
  f.click();await f.feedback.run('실측 중…',()=>{count++;});assert.equal(count,1);
  request.resolve();await one;assert.equal(f.button.disabled,true);
});

test('background idle updates stay silent, while a real asynchronous job remains visible until its terminal state',()=>{
  const f=fixture();f.e['action-feedback'].hidden=true;
  f.feedback.reflect('llm',{busy:false});assert.equal(f.e['action-feedback'].hidden,true);
  f.click();f.feedback.reflect('llm',{busy:true,label:'두 모델 비교 중…',detail:'0 / 6 호출 완료'});
  f.advance(4000);f.feedback.reflect('llm',{busy:true,label:'두 모델 비교 중…',detail:'2 / 6 호출 완료'});
  assert.match(f.e['action-feedback-detail'].textContent,/2 \/ 6/);assert.match(f.e['action-feedback-detail'].textContent,/4초/);
  f.feedback.reflect('llm',{busy:false,failed:true});assert.equal(f.e['action-feedback'].dataset.state,'error');
});

test('restoring a trigger restores its previous accessibility attributes',async()=>{
  const f=fixture();f.button.setAttribute('aria-busy','false');f.button.setAttribute('data-loading','custom');f.click();
  await f.feedback.run('조회 중…',async()=>{});
  assert.equal(f.button.getAttribute('aria-busy'),'false');assert.equal(f.button.getAttribute('data-loading'),'custom');
});
