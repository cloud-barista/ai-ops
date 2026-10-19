export const gpuVmTypes=Object.freeze(['g6.2xlarge','g6.8xlarge','g5.xlarge']);
export const cloudVmTypes=Object.freeze(['m6a.large','m6a.2xlarge','m6a.8xlarge',...gpuVmTypes]);
export const isCloudGpuType=type=>gpuVmTypes.includes(type);
// The current VM adapter chooses CPU or CUDA from the reviewed VM type.
// Hardware feasibility alone therefore cannot authorize a different runtime.
export function cloudRuntimeIssue(requirements,candidate){
 if(!candidate||typeof requirements?.accelerator?.required!=='boolean')return null;
 const gpu=isCloudGpuType(candidate.name),required=requirements.accelerator.required;
 if(gpu!==required)return {code:'cloud_cpu_gpu_requirements_mismatch',message:gpu
  ?'현재 요구사항은 GPU를 사용하지 않는 CPU 추론입니다. 자원 추천에서 CPU VM을 선택하고 배포 계획을 다시 만드세요. GPU 추론은 요구사항에서 GPU 사용을 지정한 뒤 다시 분석하세요.'
  :'현재 요구사항은 GPU 추론입니다. 자원 추천에서 GPU VM을 선택하고 배포 계획을 다시 만드세요.'};
 if(gpu&&requirements.deployment?.replicas_max!==undefined&&requirements.deployment.replicas_max!==1)return {code:'cloud_gpu_replica_bounds',message:'현재 GPU 실행은 최대 복제본 1개를 지원합니다. 요구사항의 최대 복제본 수를 1개로 수정하고 다시 분석하세요.'};
 return null;
}
export function cloudCandidatePolicy(candidate,requirements){
 const issue=cloudRuntimeIssue(requirements,candidate);
 return issue?{...candidate,selectable:false,execution_eligible:false,execution_reason:issue.message}:candidate;
}
