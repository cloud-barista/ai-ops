import test from 'node:test';
import assert from 'node:assert/strict';
import * as diagnostic from '../scripts/read-cloud-console-diagnostic.mjs';
test('earliest guard observations preserve only bounded packet header metadata',()=>{
 const value={stage:'GUARD_WATCH',at:1791973438,ssh_service:'active',journal_available:true,signals:{},packet_capture:true,packets:[{at_ms:1791973437000,connection:'aabbccddeeff',direction:'OUT',kind:'RST',ttl:64,payload_bytes:0,host:'SECRET'},{at_ms:1,connection:'SECRET',direction:'IN',kind:'DATA',ttl:1,payload_bytes:1}]};
 const parsed=diagnostic.parseBootstrapConsole('KHU_BOOTSTRAP_DIAG='+JSON.stringify(value));
 assert.equal(parsed.length,1);assert.equal(parsed[0].packet_capture,true);
 assert.deepEqual(parsed[0].packets,[{at_ms:1791973437000,connection:'aabbccddeeff',direction:'OUT',kind:'RST',ttl:64,payload_bytes:0}]);
 assert.equal(JSON.stringify(parsed).includes('SECRET'),false);
});

test('console diagnostics retain only fixed bootstrap stages and sanitized SSH counts',()=>{
 assert.equal(typeof diagnostic.parseBootstrapConsole,'function');
 const raw='ssh-ed25519 SECRET_HOST_KEY\r\nKHU_BOOTSTRAP_DIAG={"stage":"TORCH_INSTALL","at":1791964000,"ssh_service":"active","journal_available":true,"signals":{"accepted publickey":1,"connection reset":2,"private_key=SECRET":1}}\r\n';
 const result=diagnostic.parseBootstrapConsole(raw);
 assert.equal(result.length,1);
 assert.deepEqual(result[0],{stage:'TORCH_INSTALL',at:1791964000,ssh_service:'active',journal_available:true,signals:{'accepted publickey':1,'connection reset':2}});
 assert.equal(JSON.stringify(result).includes('SECRET'),false);
});

test('console diagnostics ignore unknown or malformed markers',()=>{
 assert.equal(typeof diagnostic.parseBootstrapConsole,'function');
 const raw='KHU_BOOTSTRAP_DIAG={"stage":"DELETE_ALL","at":1}\nKHU_BOOTSTRAP_DIAG=not-json\n';
 assert.deepEqual(diagnostic.parseBootstrapConsole(raw),[]);
});
test('post-ready SSH observations survive the deployment phase',()=>{
 const raw='KHU_BOOTSTRAP_DIAG={"stage":"POST_READY","at":1791971430,"ssh_service":"active","journal_available":true,"signals":{"connection reset":4,"maxstartups":0}}\n';
 assert.deepEqual(diagnostic.parseBootstrapConsole(raw),[{stage:'POST_READY',at:1791971430,ssh_service:'active',journal_available:true,signals:{'connection reset':4,'maxstartups':0}}]);
});
test('SSH failure events keep only fixed cause codes and timestamps',()=>{
 const raw='KHU_BOOTSTRAP_DIAG='+JSON.stringify({stage:'POST_READY',at:1791973438,ssh_service:'active',journal_available:true,signals:{'fatal:':1},events:[{at:1791973436,kind:'PREAUTH_TIMEOUT',message:'secret=DO_NOT_KEEP',ip:'1.2.3.4'},{at:1791973437,kind:'secret=DO_NOT_KEEP'},{at:'1791973438',kind:'KEX_RESET'}]})+'\n';
 const parsed=diagnostic.parseBootstrapConsole(raw);
 assert.deepEqual(parsed[0].events,[{at:1791973436,kind:'PREAUTH_TIMEOUT'}]);
 assert.equal(JSON.stringify(parsed).includes('DO_NOT_KEEP'),false);
 assert.equal(JSON.stringify(parsed).includes('1.2.3.4'),false);
});
test('empty later console snapshots cannot erase previously collected guest evidence',()=>{
 assert.equal(typeof diagnostic.mergeConsoleObservations,'function');
 const first={stage:'GUARD_WATCH',at:100,ssh_service:'active',journal_available:true,signals:{}};
 assert.deepEqual(diagnostic.mergeConsoleObservations([first],[]),[first]);
 assert.equal(diagnostic.mergeConsoleObservations([first],[first]).length,1);
});
