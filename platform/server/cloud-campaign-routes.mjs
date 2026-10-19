const fault=(code,status=409)=>Object.assign(Error(code),{code,status});
export function createCloudCampaignRoutes({load,childRoute}){
 return async ctx=>{
  const {request,path,value,store}=ctx;if(!path.startsWith('/api/v1/cloud-campaigns'))return;
  const s=await load(ctx),c=s.campaigns;
  if(path==='/api/v1/cloud-campaigns'){
   if(request.method==='GET')return {data:{campaigns:await c.list()}};
   if(request.method!=='POST')throw fault('method_not_allowed',405);
   if(Object.keys(value??{}).some(k=>k!=='flow_ids')||!Array.isArray(value?.flow_ids)||value.flow_ids.length!==2||value.flow_ids.some(id=>!/^flow-[\w-]+$/.test(id)))throw fault('invalid_campaign_request',400);
   return {data:await c.review(await Promise.all(value.flow_ids.map(id=>store.read(id))))};
  }
  const m=path.match(/^\/api\/v1\/cloud-campaigns\/(run-campaign-[\w-]+)\/(approve|start|expand|shrink|stop|status|connections)$/);if(!m)throw fault('not_found',404);
  const [,id,action]=m;if(request.method!==(['status','connections'].includes(action)?'GET':'POST'))throw fault('method_not_allowed',405);
  if(action==='status')return {data:await c.snapshot(id)};
  if(action==='connections')return {data:await c.connectionAudit(id)};
  if(action==='approve')return {data:await c.approve(id,value)};
  if(Object.keys(value??{}).length)throw fault('invalid_campaign_request',400);
  if(['start','expand'].includes(action)&&!s.config.enabled)throw fault('live_cloud_disabled');
  const p=await c.action(id,action);
  if(['start','expand'].includes(action)){
   const member=p.intent.members[action==='start'?0:1];
   (async()=>{try{
    for(const step of ['preflight','create'])await childRoute({...ctx,request:{method:'POST'},path:'/api/v1/flows/'+member.flow_id+'/cloud/'+step,value:{}});
   }catch{await c.action(id,'stop');await c.reconcile();}})().catch(()=>{});
  }else c.reconcile().catch(()=>{});
  return {data:p};
 };
}
