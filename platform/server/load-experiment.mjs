import {randomUUID} from 'node:crypto';import {fault} from './index.mjs';
export function createLoadExperimentRoutes({maxSeconds=20,maxRequests=8192,concurrency=32}={}){
 const jobs=new Map();let slot=Promise.resolve();const exclusive=fn=>{const next=slot.catch(()=>{}).then(fn);slot=next;return next;};
 return async({request,path,value,store,clients})=>{
  const match=path.match(/^\/api\/v1\/flows\/(flow-[\w-]+)\/load-test(\/stop)?$/);if(!match)return;
  const [,id,stop]=match;
  if(!['GET','POST'].includes(request.method)||stop&&request.method!=='POST')throw fault(405,'method_not_allowed');
  return exclusive(async()=>{
   let r=await store.read(id),job=jobs.get(id);
   if(request.method==='GET'){
    if(job)r=await store.update(id,current=>({...current,load_test:{...current.load_test,issued_requests:job.issued,completed_requests:job.completed,errors:job.errors}}));
    else if(r.load_test?.state==='RUNNING')r=await store.update(id,current=>({...current,load_test:{...current.load_test,state:'INTERRUPTED',ended_at:new Date().toISOString()}}));
    return {data:r};
   }
   if(stop){if(Object.keys(value??{}).length)throw fault(400,'invalid_load_request');if(job)job.stop=true;return {data:r};}
   if(Object.keys(value??{}).some(k=>!['selection_version','request_id'].includes(k))||!/^[a-f0-9-]{36}$/.test(value?.request_id??''))throw fault(400,'invalid_load_request');
   if(value.selection_version!==r.selection_version)throw fault(409,'stale_selection');
   if(r.input?.execution_target!=='local'||r.execution?.status!=='RUNNING'||r.replica_group?.operation_state!=='IDLE')throw fault(409,'running_local_group_required');
   if(r.load_test?.request_id===value.request_id)return {status:202,data:r};
   if(jobs.size||r.load_test?.state==='RUNNING')throw fault(409,'load_test_running');
   job={stop:false,issued:0,completed:0,errors:0};jobs.set(id,job);
   const run_id='load-'+randomUUID(),started_at=new Date().toISOString(),end=Date.now()+maxSeconds*1000,group=r.replica_group.group_id,model=r.input.model;
   try{r=await store.update(id,current=>({...current,load_test:{run_id,request_id:value.request_id,state:'RUNNING',flow_id:id,group_id:group,started_at,max_seconds:maxSeconds,max_requests:maxRequests,concurrency,issued_requests:0,completed_requests:0,errors:0},history:[...current.history,{event:'local_load_started',at:started_at,run_id}]}));}catch(e){jobs.delete(id);throw e;}
   (async()=>{
    try{
     await Promise.all(Array.from({length:concurrency},async()=>{
      while(!job.stop&&Date.now()<end&&job.issued<maxRequests){
       job.issued++;
       try{const out=await clients.groupInvoke(group,{method:'POST',path:'/infer',timeout_seconds:3,body:{texts:Array.from({length:model.batch_size??1},()=> 'The service responds quickly.')}}),v=out.result?.body;
        if(out.result?.status_code!==200||v?.flow_id!==id||v.model_revision!==model.model_revision||v.model_inference!==true)job.errors++;else job.completed++;
       }catch{job.errors++;job.stop=true;}
      }
     }));
    }finally{
     const ended_at=new Date().toISOString();try{await store.update(id,current=>({...current,load_test:{...current.load_test,state:job.errors?'FAILED':job.stop?'STOPPED':'COMPLETED',ended_at,issued_requests:job.issued,completed_requests:job.completed,errors:job.errors},history:[...current.history,{event:'local_load_finished',at:ended_at,run_id,requests:job.completed,errors:job.errors}]}));}finally{jobs.delete(id);}
    }
   })().catch(()=>{});
   return {status:202,data:r};
  });
 };
}
