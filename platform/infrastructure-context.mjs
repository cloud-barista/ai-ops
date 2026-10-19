import {candidateCardModel, modelName} from './view-model.mjs';

const number = value => new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(value);
const routeLabels = {historical:'과거 VM 비교·판단',local:'이 PC에 실제 배포·실측',cloud:'AWS VM에 실제 배포·실측'};
const roles = {
  'm6a.large':'2 vCPU·8 GiB 조건에서 CPU 추론과 배포 판단을 비교합니다.',
  'm6a.2xlarge':'8 vCPU·32 GiB 조건에서 CPU 처리량과 지연을 비교합니다.',
  'm6a.8xlarge':'32 vCPU·128 GiB 조건에서 CPU·메모리 여유와 추론 성능을 비교합니다.',
  'g6.2xlarge':'L4 1개와 8 vCPU·32 GiB 조건에서 GPU 추론을 비교할 수 있습니다.',
  'g6.8xlarge':'L4 1개와 32 vCPU·128 GiB 구성입니다. g6.2xlarge보다 CPU·RAM이 크고 GPU 수는 같습니다.',
};
export function infrastructureContextModel(record, candidate) {
  const selected = candidate ?? record?.candidates?.all?.find(c => c.candidate_id === record.selected_candidate_id);
  if (!selected) return null;
  const route = record.input?.execution_target ?? 'historical', req = record.application_profile?.requirements ?? {};
  const model = record.input?.model ?? {}, workload = record.input?.workload ?? {}, shape = selected.shape ?? {};
  const view = candidateCardModel(selected), raw = route === 'local' ? null : selected.measurement;
  const measurement = raw ? {latency:raw.batch_p95_ms,throughput:raw.throughput,
    runIds:[...new Set(raw.run_ids ?? [])],cpuCoreLimit:raw.cpu_core_limit,
    label:raw.comparison === 'historical_reference' ? '동일 조건 참고 측정' : '동일 조건 벤치마크'} : null;
  const plan = record.geon, current = selected.candidate_id === record.selected_candidate_id;
  const bound = current && plan?.correlation_id === `${record.flow_id}-s${record.selection_version}` &&
    plan?.decision?.selected_candidate_id === selected.candidate_id && plan?.deployment_plan?.selected_candidate_id === selected.candidate_id;
  const planApproved = !!bound && plan.decision.action === 'DEPLOY' && plan.guard?.status === 'APPROVED';
  const minimum = {cpu:req.compute?.cpu_cores_min,memoryMiB:req.compute?.memory_mib_min,storageGiB:req.compute?.storage_gib_min,
    gpuCount:req.accelerator?.required ? req.accelerator.count_min : 0};
  const capacity = {cpu:shape.cpu_cores,memoryMiB:shape.memory_mib,gpuCount:shape.gpu_count ?? 0,gpuModel:shape.gpu_model};
  const runtimeDevice = req.accelerator?.required ? 'GPU' : 'CPU';
  const condition = `${modelName(model.model_id ?? '')} · 배치 ${model.batch_size ?? '—'} · 길이 ${model.max_length ?? '—'} · ${runtimeDevice} 추론`;
  const minimumLabel = `${minimum.cpu ?? '—'} vCPU · ${Number.isFinite(minimum.memoryMiB) ? number(minimum.memoryMiB/1024) : '—'} GiB RAM · 저장소 ${minimum.storageGiB ?? '—'} GiB${minimum.gpuCount ? ` · GPU ${minimum.gpuCount}개` : ''}`;
  const executionMode = {historical:'mock',local:'local_process',cloud:'cloud_vm'}[route];
  const next = !selected.selectable ? '요구사항을 조정한 뒤 다시 비교합니다.' : !planApproved ? `${selected.name} 선택 → 배포 계획 생성·검증` :
    route === 'historical' ? `${selected.name} 승인 계획 → Mock 실행 기록 → 데모 관측으로 운영 판단` :
    route === 'local' ? '승인 계획 → CPU 프로세스 배포 → Prometheus 관측·운영 판단' :
    `${selected.name} 승인 계획 → 비용 검토·승인 → CPU/GPU VM 배포·실측 → 종료 확인`;
  const targets = [];
  if (Number.isFinite(workload.min_throughput)) targets.push(`${number(workload.min_throughput)} 입력/초 이상`);
  if (Number.isFinite(workload.max_batch_p95_ms)) targets.push(`배치 p95 ${number(workload.max_batch_p95_ms)} ms 이하`);
  const location = selected.spec_id?.split('+').slice(0,2).join(' · ') ?? (route === 'local' ? '로컬 PC' : 'AWS');
  return {candidateId:selected.candidate_id,name:selected.name,capacity,minimum,runtimeDevice,route,executionMode,
    routeLabel:routeLabels[route],hardware:view.hardware,condition,minimumLabel,
    role:roles[selected.name] ?? '관측된 CPU·메모리·디스크로 실제 어플리케이션을 실행합니다.',
    location,fit:view.fit,reason:selected.reason ?? '',measurement,workload:{modelId:model.model_id,revision:model.model_revision},
    benchmarkTarget:targets.join(' · '),planApproved,next,latency:view.latency,throughput:view.throughput,
    cost:workload.objective === 'work' ? view.workCost : view.cost};
}
