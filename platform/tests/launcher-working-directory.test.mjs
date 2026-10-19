import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {homedir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const root=resolve(fileURLToPath(new URL('../../',import.meta.url)));
const hostUrl=new URL('../server/local-host.mjs',import.meta.url).href;
const registrationUrl=new URL('../scripts/Register-LocalTarget.mjs',import.meta.url).href;
function fromUserHome(source){
  const result=spawnSync(process.execPath,['--input-type=module','-e',source],{cwd:homedir(),encoding:'utf8',timeout:10000});
  assert.equal(result.status,0,result.stderr||result.error?.message||result.stdout);
}

test('local PC snapshot uses project storage when launched from the user home',()=>{
  fromUserHome(`
    import assert from 'node:assert/strict';
    const {localHostSnapshot}=await import(${JSON.stringify(hostUrl)});
    const host=await localHostSnapshot();
    assert.equal(host.id,'khu-local-pc');
    assert.ok(host.disk_total>0);
    assert.ok(host.cpu_cores>0);
  `);
});

test('local target registration keeps logs and snapshot inside the project from another cwd',()=>{
  fromUserHome(`
    import assert from 'node:assert/strict';
    import fs from 'node:fs';
    import {syncBuiltinESMExports} from 'node:module';
    import {resolve} from 'node:path';
    const root=${JSON.stringify(root)},writes=[],posts=[];
    fs.promises.writeFile=async(path,data)=>{writes.push({path,data});};
    syncBuiltinESMExports();
    globalThis.fetch=async(url,options)=>{
      assert.equal(url,'http://127.0.0.1:18150/api/v1/target-profiles');
      if(options?.method==='POST'){
        posts.push(JSON.parse(options.body));
        return new Response('{}',{status:201});
      }
      return new Response('[]',{status:200});
    };
    await import(${JSON.stringify(registrationUrl)});
    assert.equal(posts.length,1);
    assert.equal(posts[0].storage.log_dir,resolve(root,'output/khu-platform-validation/runtime/app-logs'));
    assert.equal(writes.length,1);
    assert.equal(resolve(writes[0].path),resolve(root,'output/khu-platform-validation/runtime/local-host.json'));
    assert.equal(JSON.parse(writes[0].data).id,'khu-local-pc');
  `);
});

test('an explicit snapshot directory remains honored',()=>{
  fromUserHome(`
    import assert from 'node:assert/strict';
    import {randomUUID} from 'node:crypto';
    import {tmpdir} from 'node:os';
    import {join} from 'node:path';
    const {localHostSnapshot}=await import(${JSON.stringify(hostUrl)});
    const missing=join(tmpdir(),'khu-missing-'+randomUUID());
    await assert.rejects(localHostSnapshot(missing),e=>e.code==='ENOENT'&&e.path===missing);
  `);
});
