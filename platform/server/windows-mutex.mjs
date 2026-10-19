import {spawn} from 'node:child_process';import {createHash} from 'node:crypto';import {resolve} from 'node:path';
// The OS releases this mutex when its holder exits. The helper also exits on
// parent pipe EOF, so a crashed Node process cannot leave a persistent lock.
export async function holdWindowsMutex(key){
 if(process.platform!=='win32')throw Error('windows_mutex_required');
 const name='Local\\KhuPoc-'+createHash('sha256').update(resolve(key).toLowerCase()).digest('hex');
 const code=`$ErrorActionPreference='Stop';$m=New-Object System.Threading.Mutex($false,'${name}');$held=$false;try{try{$held=$m.WaitOne(10000)}catch [System.Threading.AbandonedMutexException]{$held=$true};if(-not $held){exit 2};[Console]::Out.WriteLine('LOCKED');[Console]::Out.Flush();$null=[Console]::In.ReadLine()}finally{if($held){$m.ReleaseMutex()};$m.Dispose()}`;
 const child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-Command',code],{windowsHide:true,stdio:['pipe','pipe','pipe']});child.stderr.resume();let output='';
 const exited=new Promise(done=>child.once('close',done));
 await new Promise((done,fail)=>{let settled=false;const timer=setTimeout(()=>{child.stdin.end();if(!settled){settled=true;fail(Error('owned_mutex_timeout'));}},15000);
  child.stdout.on('data',b=>{output+=b;if(!settled&&output.includes('LOCKED')){settled=true;clearTimeout(timer);done();}});child.once('error',()=>{if(!settled){settled=true;clearTimeout(timer);fail(Error('owned_mutex_unavailable'));}});child.once('close',()=>{if(!settled){settled=true;clearTimeout(timer);fail(Error('owned_mutex_unavailable'));}});
 });let released=false;return async()=>{if(released)return;released=true;child.stdin.end('\n');await exited;};
}
export async function withWindowsMutex(key,fn){const release=await holdWindowsMutex(key);try{return await fn();}finally{await release();}}
