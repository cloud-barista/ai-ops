# KHU 모델 기반 배포·운영 PoC 설계 및 API 명세

## 1. 책임 경계

KHU 모듈은 ApplicationProfile과 ResourceOps 후보, 논리적인
DeploymentContext, 운영 피드백을 입력받아 후보별 기대 보상과 정책
제안을 생성한다. 출력은 플랫폼/VM ID가 없는 Deployment Spec 수준이다.

    KHU 요구사항·후보 평가
      -> logical resource shape / operation / placement conditions
      -> INNO handoff
      -> ETRI/CSP concrete provider, region, VM realization
      -> Mock/Handoff adapter deployment status
      -> KHU optimization feedback

PoC 기본 모드는 shadow다. 모델 제안은 기록 및 비교만 하며 Go의 기존
룰과 Mock/Handoff Adapter 실행을 변경하지 않는다.

## 2. 데이터 계보와 합성 한계

입력은 runs/fca-scenarios/scenarios.sqlite3의 scenarios.input_json 및
scenario_nodes뿐이다. selected_mode, decision_json, FL/SL 휴리스틱 라벨,
fca-hf-* 실행 DB를 읽거나 학습에 사용하지 않는다.

각 원본 ID는 NEW, SPEC_DRIFT, SLO_PRESSURE, UNDERUTILIZED, HEALTHY로
확장된다. 상태별 네 개 후보는 CPU-only/GPU, 노드 수, CPU/메모리/GPU,
비용, 가용성, provider/region/label 조합을 다르게 한다. 후보-액션
결과에는 hard feasibility, success, SLO, latency, throughput, error,
resource use, cost, reward 및 oracle가 SQLite와 CSV에 저장된다.

보상은 hard-infeasible 또는 실행 실패이면 0이다. 성공이면 아래와
같다.

    reward = 0.55*SLO headroom + 0.25*performance
             + 0.10*resource efficiency + 0.10*cost efficiency

따라서 실험의 주장은 합성 CPU/GPU VM 시나리오에서의 판단 메커니즘
검증으로 한정된다. 실제 CSP 성능 또는 VM 운영 우수성을 주장하지 않는다.

별도 `runs/khu-hf-live/executions.sqlite3`는 로컬 Hugging Face cache의
실제 PyTorch inference를 실행한 원시 계측 DB다. `--profile-set diverse`는
모델 크기(BERT Tiny/Mini, ALBERT, ELECTRA, DistilBERT, BERT/RoBERTa Base,
Qwen 0.5B), batch/workload 크기(1/4/8), sequence length(64~512),
closed-loop request concurrency(1/2/4), 부하 조건, GPU replica 수(1/2)를
명시적으로 바꾼다. 2-GPU 후보는 GPU 0·1에 각각 실제 model process를
동시에 시작하고 측정하며, host/GPU telemetry와 두 물리 GPU index를 저장한다.

`prepare-live-dataset`은 이 원시 DB를 별도 DEPLOY-only placement DB로
변환한다. 보상은 실제 latency/throughput/RSS/telemetry와 동일 보상식을
사용한다. UPDATE/SCALE 전후의 실제 관측은 아직 없으므로 이 artifact가
UPDATE/SCALE 정책을 학습했다고 주장하지 않으며 shadow placement에만 쓴다.

## 3. 모델과 안전 정책

후보-액션 보상 예측기는 seed 42, 200 trees의
RandomForestRegressor다. 입력은 응용 요구사항, 후보 인프라, 현재
replica/spec 상태, CPU/메모리/GPU/지연/처리량/오류율, action,
desired replica 및 delta다.

실측 placement feature에는 workload request concurrency, sequence length,
load condition과 candidate node/GPU replica 수도 추가된다. 반복 계측은
같은 workload ID를 source group으로 유지하여 70/15/15 분할에서 train/test
누수를 막는다.

원본 source_scenario_id 단위로 70/15/15 train/validation/test 그룹
분할한다. joblib artifact와 manifest에는 artifact SHA-256, dataset hash,
feature schema, feature range/category, split, training 설정과 지표를
기록한다.

신뢰도는 forest tree prediction spread과 입력 범위·범주 이탈 비율을
결합한다. confidence < 0.70 또는 OOD ratio > 0.20이면 ABSTAIN이다.
Go guard는 hard feasibility, replica min/max, SLO violation, cooldown,
low confidence/OOD, timeout 및 policy 오류보다 항상 우선한다.

## 4. 정책 액션과 UPDATE

정책 액션은 DEPLOY, UPDATE, SCALE_OUT, SCALE_IN, KEEP다. REJECT와 RETRY는
입력 또는 실행 예외로 남긴다.

DeploymentContext의 deployment_ref는 논리 식별자이며 VM ID가 아니다.
current_spec_digest와 새 DesiredDeploymentSpec digest가 다르거나
spec_drift=true이면 UPDATE, 그렇지 않으면 KEEP를 사용한다. Desired spec은
operation, application artifact, resource shape, placement required labels,
preferred labels 및 anti-affinity를 담는다. UPDATE는 Mock/Handoff에서 spec
revision을 기록하여 검증하며 실제 VM을 수정하지 않는다.

## 5. HTTP API

Endpoint:

    POST /api/v1/khu-policy/evaluate

필수 입력:

    {
      "phase": "planning",
      "application_profile": { "...ApplicationProfile..." },
      "resource_recommendation": {
        "candidates": [ { "...ResourceOps candidate..." } ]
      }
    }

선택 입력은 deployment_context와 optimization_feedback이다. operation
phase에서 피드백은 resource.cpu_average_percent,
resource.memory_peak_mib, resource.accelerator_average_percent,
inference.latency_p95_ms, inference.throughput_rps,
inference.error_rate_percent를 사용한다.

응답은 schema_version, mode=shadow, phase, model_version,
dataset_sha256, artifact_sha256, proposal 및 candidate_evaluations를 담는다.
proposal에는 action(또는 ABSTAIN), selected_candidate_id,
desired_replicas, expected_utility, confidence, ood_ratio,
abstain_reason이 포함된다.

Go integration은 KHU_POLICY_MODE=shadow와 KHU_POLICY_URL이 모두 설정된
경우에만 endpoint를 호출한다. 호출 실패는 ABSTAIN assessment로 누적하고
룰 기반 결과를 즉시 반환한다.

## 6. 검증 절차

    ./scripts/generate-khu-deployment-scenarios.sh
    ./scripts/train-khu-policy.sh
    KHU_POLICY_MODE=shadow KHU_POLICY_URL=http://127.0.0.1:18500 ./scripts/serve-khu-policy.sh

테스트는 lifecycle/action/CPU-GPU balance, infeasible reward와 oracle
제외, CSV/SQLite 저장, 그룹 분할, OOD abstain, 모델 artifact 검증 및 기존
static-scenario 회귀를 포함한다. holdout MAE, oracle regret, action macro-F1,
SLO violation, infeasible selection, OOD abstain은 manifest에 보관하며
현재 룰 baseline과 병렬 비교한다.
