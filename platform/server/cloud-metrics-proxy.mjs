// A scrape must not run AppDeploy's serialized VM status/SSH health probe.
// Current group membership and the returned worker's bound identity suffice
// for this read-only path; explicit status actions still perform the full probe.
export function boundCloudMetrics(raw,owned,group,child){
 if(typeof raw!=='string'||raw.length>65536)return false;
 const binding=owned.worker_bindings?.[child.replica_id],model=owned.input?.model;
 if(!binding||!Number.isInteger(binding.pid)||binding.pid<1||!Number.isFinite(binding.worker_started_at)||!model)return false;
 const expected={flow_id:owned.flow_id,group_id:group.group_id,replica_id:child.replica_id,worker_pid:String(binding.pid),device:binding.device,...Object.fromEntries(['model_id','model_revision','batch_size','max_length'].map(k=>[k,String(model[k])]))};
 let samples=0;
 for(const line of raw.split('\n')){
  if(!line.trim()||line.startsWith('#'))continue;
  const match=line.match(/^(khu_app_[a-zA-Z0-9_:]+)\{(.*)\} ([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)$/);
  if(!match||!Number.isFinite(Number(match[3])))return false;
  const labels={},pattern=/([a-zA-Z_][a-zA-Z0-9_]*)=("(?:[^"\\]|\\.)*")(?:,|$)/gy;
  let cursor=0,part;
  while((part=pattern.exec(match[2]))){if(Object.hasOwn(labels,part[1]))return false;try{labels[part[1]]=JSON.parse(part[2]);}catch{return false;}cursor=pattern.lastIndex;}
  if(cursor!==match[2].length||Object.entries(expected).some(([key,value])=>labels[key]!==value)||!Object.hasOwn(labels,'worker_started_at')||Number(labels.worker_started_at)!==binding.worker_started_at)return false;
  samples++;
 }
 return samples>0;
}
