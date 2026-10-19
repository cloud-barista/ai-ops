import os from 'node:os';import {mkdir,statfs} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
export const localRuntimeDirectory=fileURLToPath(new URL('../../output/khu-platform-validation/runtime/',import.meta.url));
const totals=()=>os.cpus().reduce((a,c)=>({idle:a.idle+c.times.idle,total:a.total+Object.values(c.times).reduce((n,v)=>n+v,0)}),{idle:0,total:0});
let previous=totals();let utilization=0;
setInterval(()=>{const current=totals(),delta=current.total-previous.total;if(delta>0)utilization=100*(1-(current.idle-previous.idle)/delta);previous=current;},1000).unref();
export async function localHostSnapshot(directory=localRuntimeDirectory){
  if(directory===localRuntimeDirectory)await mkdir(directory,{recursive:true});
  const disk=await statfs(directory);return {id:'khu-local-pc',spec_id:'local+windows+pc',hostname:os.hostname(),cpu_cores:os.cpus().length,cpu_utilization:utilization,memory_total:os.totalmem(),memory_available:os.freemem(),disk_total:disk.blocks*disk.bsize,disk_available:disk.bavail*disk.bsize,observed_at:new Date().toISOString()};
}
