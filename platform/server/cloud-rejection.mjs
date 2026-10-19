const check=(ok,code='creation_rejection_unverified')=>{if(!ok)throw Error(code);};
export async function auditRejectedCreation({run,bridge,provider,clock=Date.now}){
 const identity=await bridge('failed_create',run.intent);check(identity.infra_id===run.intent.infra_id&&identity.intent_sha256===run.intent.intent_sha256&&/^tb[a-z0-9]{18,30}$/.test(identity.provider_name??'')&&identity.reason==='INSUFFICIENT_INSTANCE_CAPACITY');
 const name=identity.provider_name,start=Date.parse(run.approval?.consumed_at);check(Number.isFinite(start));let token,events=[],complete=false;
 for(let n=0;n<10;n++){
  const data=await provider('cloudtrail','lookup-events',['--lookup-attributes','AttributeKey=EventName,AttributeValue=RunInstances','--start-time',new Date(start-5000).toISOString(),'--end-time',new Date(clock()).toISOString(),'--max-results','50',...token?['--next-token',token]:[]]);
  for(const entry of data.Events??[]){const event=JSON.parse(entry.CloudTrailEvent),names=(event.requestParameters?.tagSpecificationSet?.items??[]).flatMap(t=>(t.tags??[]).filter(x=>x.key==='Name').map(x=>x.value));if(names.includes(name))events.push(event);}
  token=data.NextToken;if(!token){complete=true;break;}
 }
 check(complete&&events.length>0);
 for(const event of events){check(Date.parse(event.eventTime)>=start-5000&&event.errorCode==='Server.InsufficientInstanceCapacity'&&event.requestParameters?.instanceType===run.intent.candidate_id&&event.requestParameters.instancesSet?.items?.[0]?.imageId===run.review.image_id&&!(event.responseElements?.instancesSet?.items?.length));}
 const filter='Name=tag:Name,Values='+name;
 const instances=await provider('ec2','describe-instances',['--filters',filter]);check((instances.Reservations??[]).every(r=>!(r.Instances?.length)));
 const volumes=await provider('ec2','describe-volumes',['--filters',filter]);check(volumes.Volumes?.length===0);
 const addresses=await provider('ec2','describe-addresses',['--filters',filter]);check(addresses.Addresses?.length===0);
 return {confirmed:true,not_created:true,infra_id:identity.infra_id,intent_sha256:identity.intent_sha256,provider_name:name,reason:identity.reason,event_ids:events.map(e=>e.eventID),source:'owned-tumblebug-failure+aws-cloudtrail+ec2-resource-audit',checked_at:new Date(clock()).toISOString()};
}
