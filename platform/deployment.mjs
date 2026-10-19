import {cloudPreparationFailed,cloudPreparationLabel} from './cloud-state.mjs';
function deploymentPrerequisite(receipt,inputDirty){
  if(inputDirty)return {title:'변경한 조건을 다시 분석하세요',description:'요구사항을 다시 분석한 뒤 인프라 선택과 계획을 이어서 진행하세요.',nextHref:'#application',nextLabel:'변경한 요구사항 분석으로 이동'};
  if(!receipt?.selected_candidate_id)return receipt?.application_profile
    ?{title:'실행할 인프라를 선택하세요',description:'분석한 요구사항을 만족하는 실행 자원을 선택하세요.',nextHref:'#infrastructure',nextLabel:'인프라 선택으로 이동'}
    :{title:'실행할 모델과 요구사항을 정하세요',description:'요구사항 분석을 먼저 진행하면 인프라를 선택할 수 있습니다.',nextHref:'#application',nextLabel:'어플리케이션 요구사항 입력'};
  if(!receipt.geon)return {title:'선택한 인프라의 배포 계획을 먼저 만드세요',description:'인프라 선택 화면의 «이 인프라로 계획 만들기»를 누르면 실행 가능 여부를 확인합니다.',nextHref:'#infrastructure',nextLabel:'배포 계획 만들기로 이동'};
  if(receipt.geon.decision?.action!=='DEPLOY'||receipt.geon.guard?.status!=='APPROVED')return {title:'배포 판단 결과를 확인하세요',description:'배포 가능과 조건 확인 통과가 확인돼야 다음 실행 단계로 진행할 수 있습니다.',nextHref:'#planning',nextLabel:'배포 판단 결과로 이동'};
  return null;
}
export function deploymentViewModel(receipt,{target='local',busy=false,inputDirty=false}={}){
  if((receipt?.input?.execution_target??target)==='cloud'){
    if(cloudPreparationFailed(receipt))return {local:false,cloud:true,approved:receipt?.geon?.guard?.status==='APPROVED',canDeploy:false,canRestart:receipt.cloud.state==='CLEANED'&&receipt.cloud.cleanup?.confirmed===true&&!busy,status:receipt.cloud.state,title:cloudPreparationLabel(receipt),description:receipt.cloud.failure?.code==='cloud_cleanup_in_progress'?'VM 종료 요청으로 응용 준비가 중단됐습니다. 분석과 계획은 보관돼 있으므로 아래 현재 비용 검토부터 다시 실행할 수 있습니다.':receipt.cloud.cleanup?.confirmed===true?'어플리케이션 배포는 완료되지 않았습니다. 생성한 VM과 확인 대상 부속 자원은 정리됐습니다. 실패 단계는 아래 실행 상세에서 확인할 수 있습니다.':'어플리케이션 배포 준비에 실패했습니다. 아래에서 VM 종료·정리 상태를 확인하세요.',nextHref:null,nextLabel:'배포 준비 결과 확인'};
    const state=receipt?.cloud?.state??'WAITING',prerequisite=state==='WAITING'?deploymentPrerequisite(receipt,inputDirty):null;return {local:false,cloud:true,approved:receipt?.geon?.guard?.status==='APPROVED',canDeploy:false,canRestart:state==='CLEANED'&&!busy,status:state,title:({PREPARING:'클라우드 VM의 모델·어플리케이션 준비 중',RUNNING:'클라우드 VM에 어플리케이션 배포 완료',CLEANED:'클라우드 VM 종료·정리 완료',CREATE_UNKNOWN:'클라우드 VM 생성 응답 확인 중',CLEANUP_PENDING:'클라우드 VM 실제 종료 확인 중'})[state]??'클라우드 VM·어플리케이션 실행 준비',description:'아래에서 현재 비용 검토, 승인, 준비 검사와 VM·어플리케이션 실행을 진행합니다. 실행 후에는 어플리케이션·VM 종료·정리 확인으로 마칩니다.',nextHref:null,nextLabel:'배포 계획 확인',...prerequisite};
  }
  const local=(receipt?.input?.execution_target??target)==='local';
  const approved=local&&receipt?.selected_candidate_id==='khu-local-pc'&&receipt.geon?.decision?.action==='DEPLOY'&&receipt.geon?.guard?.status==='APPROVED'&&receipt.geon?.deployment_plan?.selected_candidate_id===receipt.selected_candidate_id;
  const execution=receipt?.execution,prepared=receipt?.appdeploy;
  const canDeploy=!!approved&&!busy&&!inputDirty&&!execution&&(!prepared||prepared.status==='PREPARED');
  let status='WAITING',title='배포할 어플리케이션을 준비하세요',nextHref='#application',nextLabel='어플리케이션 요구사항 입력',description='BERT Tiny와 필요한 자원을 정하고 요구사항 분석을 진행하세요.';
  if(!local){
    status='COMPARISON';description='과거 VM 비교 경로에서는 실제 VM을 만들지 않고 계획의 모의 실행 기록을 저장합니다.';
    const prerequisite=deploymentPrerequisite(receipt,inputDirty);
    if(inputDirty||!execution&&prerequisite)({title,description,nextHref,nextLabel}=prerequisite);
    else if(execution){title='모의 실행 기록을 저장했습니다';nextHref='#operation';nextLabel='예시 관측으로 운영 판단';}
    else{title='모의 실행을 기록할 준비가 됐어요';nextHref=null;}
  }
  else if(inputDirty){title='변경한 조건을 다시 분석하세요';}
  else if(execution){status=execution.status;title=({RUNNING:'어플리케이션 배포 완료 · 실행 중',STOPPED:'어플리케이션 실행 종료',COMPLETED:'어플리케이션 실행 완료'})[status]??'배포 상태를 확인하세요';description=status==='RUNNING'?'선택한 BERT Tiny가 실제 CPU 프로세스로 실행 중입니다. 다음 단계에서 추론 성능을 측정하세요.':status==='UNKNOWN'?'기존 실행의 실제 상태를 아직 확인하지 못했습니다. 상태 조회로 확인하세요.':'이 Flow의 배포·측정 기록을 보관했습니다. 다시 배포하려면 새 실행을 시작하세요.';if(status==='RUNNING'){nextHref='#operation';nextLabel='추론 성능 측정으로 이동';}else nextLabel='새 실행에서 어플리케이션 조건 입력';}
  else if(prepared&&prepared.status!=='PREPARED'){status=prepared.status;title='기존 배포 응답 확인 중';description='상태·로그 조회로 기존 요청의 처리 결과를 확인하세요.';nextHref=null;}
  else if(approved){status='READY';title='선택한 자원에 배포할 준비가 됐어요';description='배포 버튼을 누르면 어플리케이션 등록, 승인된 계획 전달, 실제 프로세스 배포를 차례로 수행합니다.';nextHref=null;}
  else if(receipt?.selected_candidate_id){({title,description,nextHref,nextLabel}=deploymentPrerequisite(receipt,inputDirty)??{title:'배포 계획을 확인하세요',nextHref:'#planning',nextLabel:'배포 계획으로 이동',description:'선택한 인프라와 승인된 계획의 실행 조건을 확인하세요.'});}
  else if(receipt?.application_profile){title='실행할 인프라를 선택하세요';nextHref='#infrastructure';nextLabel='인프라 선택으로 이동';description='분석한 요구사항을 만족하는 실행 자원을 선택하세요.';}
  const canRestart=local&&!busy&&!!execution&&['STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED','EXTERNAL_API_FAILED'].includes(execution.status);
  return {local,approved:!!approved,canDeploy,canRestart,status,title,description,nextHref,nextLabel};
}

export async function deploySelectedApp(receipt,request,onReceipt=()=>{}){
  if(!deploymentViewModel(receipt).canDeploy)throw Error('approved_local_plan_required');
  let current=receipt;
  const path=`/flows/${receipt.flow_id}/appdeploy/`;
  if(!current.appdeploy){current=await request(path+'prepare',{selection_version:current.selection_version});onReceipt(current);}
  if(!deploymentViewModel(current).canDeploy)throw Error('approved_local_plan_required');
  current=await request(path+'submit',{selection_version:current.selection_version});onReceipt(current);
  return current;
}
