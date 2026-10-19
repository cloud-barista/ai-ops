import test from 'node:test';import assert from 'node:assert/strict';import {spawn} from 'node:child_process';import {randomUUID} from 'node:crypto';import {holdWindowsMutex} from '../server/windows-mutex.mjs';
test('live owner excludes another writer and crashed writer releases OS lock',async()=>{
 const key='khu-test-'+randomUUID(),release=await holdWindowsMutex(key);let acquired=false;const next=holdWindowsMutex(key).then(r=>{acquired=true;return r;});await new Promise(r=>setTimeout(r,100));assert.equal(acquired,false);await release();await (await next)();
 const code=`import {holdWindowsMutex} from ${JSON.stringify(new URL('../server/windows-mutex.mjs',import.meta.url).href)};await holdWindowsMutex(${JSON.stringify(key)});console.log('HELD');setInterval(()=>{},1000);`;
 const child=spawn(process.execPath,['--input-type=module','-e',code],{windowsHide:true,stdio:['ignore','pipe','ignore']});await new Promise((done,fail)=>{child.stdout.on('data',b=>{if(String(b).includes('HELD'))done();});child.once('error',fail);});const exit=new Promise(done=>child.once('close',done));child.kill();await exit;await (await holdWindowsMutex(key))();
});
