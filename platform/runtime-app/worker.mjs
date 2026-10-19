import {createServer} from 'node:http';import {createHash} from 'node:crypto';
const started=Date.now();let completed=0;let lastCpu=process.cpuUsage();let lastAt=performance.now();
const server=createServer((request,response)=>{
  const json=value=>{response.writeHead(200,{'content-type':'application/json'});response.end(JSON.stringify(value));};
  if(request.url==='/healthz')return json({status:'ok',pid:process.pid,app:'khu-local-execution-worker',model_inference:false});
  if(request.url==='/metrics'){const elapsed=(performance.now()-lastAt)/1000,cpu=process.cpuUsage(lastCpu);lastCpu=process.cpuUsage();lastAt=performance.now();return json({source:'local_process',pid:process.pid,completed,cpu_core_percent:Math.min(100,(cpu.user+cpu.system)/1e6/elapsed*100),memory_rss_mib:process.memoryUsage().rss/1048576,uptime_seconds:(Date.now()-started)/1000});}
  if(request.url==='/work'){let value='KHU execution validation';for(let i=0;i<300;i++)value=createHash('sha256').update(value).digest('hex');completed++;return json({completed,digest:value,model_inference:false});}
  response.writeHead(404);response.end();
});
server.listen(18160,'127.0.0.1',()=>console.log(JSON.stringify({event:'worker_started',pid:process.pid,url:'http://127.0.0.1:18160',model_inference:false})));
server.on('error',error=>{console.error(error.code);process.exit(1);});
const heartbeat=setInterval(()=>console.log(JSON.stringify({event:'heartbeat',pid:process.pid,completed,uptime_seconds:(Date.now()-started)/1000})),10000);
setTimeout(()=>{console.log('worker reached 15 minute local execution limit');clearInterval(heartbeat);server.close(()=>process.exit(0));},900000);
