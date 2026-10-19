# AI 응용 배포 제어 추론 최적화 전략 설계서
English title: AI Application Deployment and Control Optimization Strategy

개정일: 2026-10-14. 기존 Guard 및 AppDeploy 계약 설명에 최적화 목표, 연구 방법, 피드백 활용, 평가 기준과 결과를 보완했습니다. 수치는 기존 산출물을 검산한 값이며, 이번 문서 개정에서 새로운 성능 실험이나 클라우드 배포를 수행하지 않았습니다.

## 1 설계 목적

본 설계는 AI 응용의 요구사항과 자원 추천 결과를 받아 배포 가능 여부를 판단하고, 실행 결과와 성능 관측으로 후속 운영 판단을 만드는 경희대 연구 프로토타입을 정의합니다. 목표는 자원 제약과 판단 의미를 유지하면서 판단에 필요한 계산 시간을 줄이고, 관측 결과를 이용해 판단 입력의 신뢰도를 높이는 것입니다. 실제 응용의 SLO 개선은 별도 실행 실험으로 평가합니다.

정식 입력은 `ApplicationProfile + ResourceRecommendation`입니다. 자연어 요구 분석은 `llmop`, 자원 수집·필터·점수화·추천은 `resource_ops`가 담당하며, deployment-agent가 이를 다시 수행하지 않습니다. 배포 판단 Agent와 운영 최적화 Agent의 출력은 독립된 Go Guard로 검증합니다. 승인된 명세도 별도 실행 또는 변경 적용 요청을 거쳐야 실제 실행됩니다. [E1, E2]

이 문서는 현재 구현, 오프라인 연구에서 채택한 계산 방법, 후속 평가를 위한 설계 조건을 구분합니다. 연구용 형상 추정이나 GPU 위험 확률을 실제 VM 배포·스케일 승인으로 바꾸어 해석하지 않습니다.

## 2 구성

| 구성 | 역할 |
| --- | --- |
| 배포 판단 Agent | Profile과 추천 결과를 결합해 `DEPLOY / REJECT / RETRY` 판단과 계획 생성 |
| 운영 최적화 Agent | 배포 상태와 성능 피드백을 받아 `KEEP / SCALE_OUT / SCALE_IN` 권고 |
| 독립 Go Guard | 입력 연결 관계, 자원 제약, 허용 Action 및 운영 관측의 유효성 검증 |
| Agent Registry와 Dispatcher | 역할·capability·허용 Action을 확인하고 등록된 판단 Agent 실행 |
| AppDeploy 연계 | 승인된 초기 명세의 투영과 명시적 전송, 상태 조회. 실제 변경 적용은 별도 요청 |
| 연구용 성능 예측기 | 알려진 응용·형상의 성능 추정과 후보 비교. 현재 운영 판단과 별도 평가 |

이전 자연어 Deployment Planner CLI는 호환·연구 경로입니다. 해당 경로의 Request Guard, Manifest Guard와 AppDeploy 계약은 4·6·9절에 보존합니다. 이를 정식 서버 경로의 자체 요구 분석 기능으로 설명하지 않습니다.

## 3 처리 흐름

```text
ApplicationProfile + ResourceRecommendation
  -> 입력 식별자와 자원 조건 확인
  -> 등록된 배포 판단 Agent 실행
  -> DEPLOY / REJECT / RETRY
  -> 독립 Go Guard 검증
  -> 승인된 최초 명세 Revision 1
  -> 별도 실행 요청 -> 실행 상태와 실제 성능 관측
  -> 운영 최적화 Agent -> Scaling Guard 검증
  -> KEEP 또는 승인된 변경 명세 Revision 2 이후
  -> 별도 변경 적용 요청 -> 실제 복제본 수와 후속 관측 확인
```

`KEEP`은 새 변경 명세나 실제 복제본 증감을 의미하지 않습니다. Revision 생성, 실행 요청 접수, 실제 프로세스 준비 완료와 SLO 충족은 각각 다른 확인 단계입니다. [E1, E2, E3]

## 4 AppDeploy 호환 Manifest

기존 자연어 Planner 호환 경로의 출력은 AppDeploy 계약인 `deployment.khu.ai/v1alpha1` 형식을 사용합니다. 정식 판단 결과인 `DesiredDeploymentSpec` 및 그 Revision과 이 호환 Manifest의 역할을 구분합니다. 승인 명세를 실행 계약으로 투영할 때도 계약이 보장하지 못하는 SLO나 비용 한도를 달성했다고 표현하지 않습니다. [E2]

```json
{
  "schema_version": "deployment.khu.ai/v1alpha1",
  "kind": "DeploymentManifest",
  "spec": {
    "app_version_id": "appver-llm-inference-v1",
    "accelerator": "nvidia",
    "resources": {
      "cpu": "4",
      "memory": "16Gi",
      "gpu": "1",
      "storage": "20Gi"
    },
    "requested_by": "ai-ops-geon-planner"
  }
}
```

`target_profile_id`는 선택 사항이며 Target hint로만 사용합니다. 최종 Target 선택은 AppDeploy가 수행합니다.

## 5 추론 최적화 전략

### 5.1 최적화 목표와 판단 변수

최적화는 다음 순서로 평가합니다. 먼저 입력·자원·권한 제약 위반을 막고, 그 조건을 만족하는 후보에서 SLO와 자원 사용을 평가합니다. 동일한 판단을 더 빠르게 계산하는 변경은 별도 실험으로 검증합니다. 이 우선순위는 본 설계의 평가 원칙이며, 모든 항목을 합친 단일 목적함수가 현재 플랫폼에 구현됐다는 뜻은 아닙니다.

| 목표 | 판단 또는 측정 대상 | 현재 확인 범위 |
| --- | --- | --- |
| 잘못된 승인 방지 | 자원 부족, 입력 불일치, 오래된 관측, 허용 범위 초과 | Guard 구현과 로컬 거부 시험 |
| 요구 성능에 맞는 자원 선택 | CPU·RAM·GPU·복제본 후보와 SLO | 추천 기능 및 연구용 형상 추정. 실제 최소 VM 형상은 미입증 |
| 판단 계산 시간 단축 | 후보 채점과 지원 범위 검사 등 함수 전체 시간 | 저장 성능 예측기의 같은 호스트 warm 오프라인 비교 |
| 피드백으로 위험 추정 보완 | 완료된 과거 관측을 반영한 위험 확률 | 공개 GPU 추적 자료의 별도 연구. 실제 제어 정책 효과는 미입증 |
| 불필요한 변경 억제 | 복제본 상·하한, KEEP, Revision과 실제 적용 구분 | 로컬 기능 재현성 확인. 장기 진동 억제 효과는 미평가 |

배포 판단의 변수는 후보 자원과 실행 요구량, 운영 판단의 변수는 허용 범위 안의 목표 복제본 수입니다. CSP·리전 선택과 VM 생성은 실행 측 연계 범위입니다. 실제 가격과 가용량에 출처가 없으면 비용 최소화나 가용 용량을 계산한 것으로 간주하지 않습니다.

### 5.2 자원 조건과 판단 검증

| 판단 항목 | 전략 |
| --- | --- |
| CPU | Profile의 최소 core와 후보 총량·가용량 비교. 호환 Manifest에서는 정수 문자열로 표현 |
| Memory | Profile의 메모리 요구와 후보 용량·단위 확인. 호환 Manifest는 `Mi/Gi/Ti` 단위 사용 |
| GPU | Profile의 필요 여부·개수와 후보 조건 비교. 호환 Manifest의 accelerator와 개수 일관성 검사 |
| Storage | Profile에 저장공간 요구가 있으면 후보의 총량·가용량 확인 |
| Target | 추천 순위와 적합성을 확인하고, 실제 Target 준비 상태와 실행은 AppDeploy 연계에서 확인 |

LLM을 사용한 판단도 후보 계획으로 취급하며 형식과 자원 제약을 별도로 검증합니다. 규칙 기반 판단, LLM 판단, LLM과 Guard의 결합은 같은 입력에서 승인 결과와 위반 여부를 비교해야 합니다. 요구사항 분석용 LLM 비교와 배포 판단 비교는 별도 실험입니다. 이 문서에서는 세 방법의 성능 우위가 확정됐다고 주장하지 않습니다.

### 5.3 판단 의미를 보존하는 계산 최적화

기존 연구는 저장된 성능 예측기의 중복 계산을 줄이는 방법을 단계적으로 시험했습니다. 동일 후보의 반복 SLO 평가에는 검증·예측 재사용을 적용하고, 여러 후보는 묶어 채점합니다. v7 연구 경로는 최대 256격자를 묶고, 지원되는 입력 행이 1,024개 이상일 때 두 연속 행 구간으로 나눕니다. 각 구간의 200개 ExtraTrees 예측은 원래 트리 순서대로 더해 수치와 판단을 보존합니다. 입력은 한 번 검증하며, 합친 예측 실패 시 기존 격자별 예측으로 돌아갑니다. [E4, E5]

모델 가중치, 전처리, 지원 범위, SLO 비교와 후보 선택 규칙은 바꾸지 않습니다. 평가 단위는 전체 판단 함수이며 worker 생성·종료 비용을 포함합니다. 모델 적재, 네트워크, 실제 AI 응용 추론과 VM 실행은 측정에서 제외됩니다. 이는 연구용 Python 계산 경로의 채택 결과이며, Go 판단 API나 PoC 화면의 지연 개선으로 자동 연결되지 않습니다.

### 5.4 실행 피드백의 현재 활용과 연구 확장

현재 PoC는 실행 상태와 계측 결과를 같은 Flow의 운영 판단 입력에 연결하고, 승인된 변경을 별도 적용한 뒤 실제 복제본 수를 확인합니다. 2026-10-13 시험은 동일한 호출 경계의 Client HTTP p95를 사용해 유지·확장·축소를 확인했습니다. 이 기능은 관측을 다음 판단에 사용하는 것이며, 모델을 온라인으로 재학습했다는 뜻은 아닙니다. [E3]

피드백의 학습 활용은 별도 연구입니다. Philly GPU 추적 실험에서는 전날 완료된 결과로 기계별 위험 확률을 보정했습니다. 미사용 41기계 시험에서 원래 모델 대비 Brier가 0.086988에서 0.082766으로 낮아졌습니다. 이 결과는 다음 30분 GPU 고사용률 위험에 한정하며 실제 VM 증감의 효과를 입증하지 않습니다. 정답 전달 시각이 없어 완료 즉시 사용할 수 있다고 가정한 한계도 남습니다. 같은 결과를 사용한 강한 보정 기준선의 사후 비교는 더 낮은 Brier를 보여, 해당 근사식의 고유 우위는 주장하지 않습니다. [E6]

실제 조치 효과를 연구하려면 다음 항목을 연결해 수집해야 합니다. 이는 연구 데이터 계약의 보완 조건이며 기존 API에 모두 구현됐다는 뜻은 아닙니다. [E7]

| 연결 항목 | 필요한 정보 | 사용하는 이유 |
| --- | --- | --- |
| 요구와 판단 | Flow·Profile·자원 ID, 정책·모델 버전, 판단 시각 | 어떤 입력으로 어떤 결정을 내렸는지 재현 |
| 관측 | 지표 정의·단위·출처, 관측 창, observed_at·available_at | 미래 정보 누출과 오래된 값 사용 방지 |
| 명세와 실행 | Revision·decision_id·실행 action_id, 요청·준비 완료 시각 | 판단 승인과 실제 조치를 구분 |
| 조치 결과 | 실제 준비 복제본, 동일 경계 지연·처리량·오류, 자원·비용 | 실행 뒤 요구 성능과 비용을 평가 |

입력은 판단 시점에 이용 가능했던 값만 사용합니다. 원본의 관측 시각과 수집기가 값을 이용할 수 있는 시각을 구분하고, 무관측을 0으로 바꾸지 않습니다. 출처·지원 범위가 없는 연구 정책 입력은 `ABSTAIN`과 사유를 보존합니다. 연구용 `ABSTAIN`은 정식 배포 Agent의 `DEPLOY / REJECT / RETRY` 계약에 새 Action을 추가한 것이 아닙니다. [E7, E8]

### 5.5 평가 기준

| 평가 구분 | 비교와 지표 | 채택 또는 보고 기준 |
| --- | --- | --- |
| 계산 최적화 | 동일 입력의 기존 v6와 고정 v7, 9쌍 실행 | 모든 응답의 정규 JSON 정확 일치 |
| 계산 시간과 비용 | wall time, 짝별 속도비, CPU 누적 시간, peak RSS | 고유 격자 속도비 중앙 ≥ 1.10, 공유·복사 입력 악화 ≤ 10%, RSS 증가 ≤ 20 MiB |
| 위험 신호 | 동결 모델, 전역 보정, 기계별 보정 | Brier·AP·기계별·시간 구간별 결과와 규약의 공동 게이트 보고 |
| PoC 기능 | 새 배포 3회, 부하 5단계, 회차별 거부 3건 | 예상 Action, 실제 복제본, KEEP의 명세 유지, 거부 시 상태 보존, 종료 확인 |
| 실제 제어 효과 | 같은 응용과 지표 경계의 조치 전후 및 비교 정책 | SLO 위반 비율·지속시간, 처리량·오류, 준비 지연, 자원·비용을 별도 평가 예정 |

계산 최적화의 정확 일치는 허용오차 없이 전체 응답 JSON으로 판정합니다. 수치 경계, 잘못된 입력, 미지원 조건, 실패 후 재시도도 검사합니다. 속도비 중앙값과 두 실행시간 중앙값의 몫은 서로 다를 수 있어 구분해 보고합니다. 계산 시간 개선과 응용 SLO 개선을 하나의 성능 지표로 합치지 않습니다. [E4, E5]

실제 제어 효과를 평가할 때는 시험 전에 부하·입력 복잡도·측정 창·기준 정책을 고정하고 같은 호출 경계의 지표를 사용합니다. Client HTTP p95와 Prometheus 서버 histogram p95를 섞어 전후 개선률을 계산하지 않습니다. 비개입 비교 구간이나 대조 정책이 없으면 조치와 결과의 관측 연관성으로 보고합니다. 향후 학습 평가에서는 응용·모델 제외 분할과 시간순 분할을 구분하고, 같은 사건의 반복 표본이나 겹친 미래 창이 양쪽에 들어가지 않도록 합니다. [E3, E7]

### 5.6 연구 결과와 적용 결정

| 근거와 조건 | 확인 결과 | 적용 결정과 한계 |
| --- | --- | --- |
| v7 오프라인 계산 확인, 고유 575격자·9쌍 | wall 중앙 362.711 → 324.155 ms, 10.63% 감소. 짝별 속도비 중앙 1.136배 | 같은 호스트 연구 경로 채택. 플랫폼 지연·VM 배포·응용 SLO 성과는 아님 |
| 같은 v7 확인의 자원 비용 | CPU 중앙 362.708 → 430.657 ms, 18.73% 증가. peak RSS +0.047 MiB | 계산 시간과 CPU 비용의 교환관계를 명시. 모든 호출의 가속을 보장하지 않음 |
| Philly 전날 보정, 미사용 41기계·685,940판단 | Brier 0.086988 → 0.082766, 4.85% 감소. 35/41기계 개선 | 위험 신호의 제한적 연구 결과. 실제 SLO·필요 VM 수·스케일 효과와 별도 |
| 로컬 BERT Tiny 기능 시험, 3회차 | 판단 15건, 유효성 거부 9건, 실측 요청 3,840건. 모든 회차 종료 후 복제본 0 | 단일 PC의 기능 재현성 확인. 반복 고정 입력이며 독립 입력 3,840개가 아님 |

v7의 고유 격자 확인에서는 9쌍 중 7쌍이 빨랐고 속도비 범위는 0.956~1.420배였습니다. 같은 실험에서 정확 JSON, 시간, 메모리 게이트를 통과했으나 통계적 유의성이나 다른 머신의 일반화를 입증하지 않았습니다. 일반 forest 트리 병렬화는 마지막 자리 수치가 바뀌었고, 고정 순서 트리 병렬화와 4-worker 행 분할 후보는 시간 기준을 통과하지 못해 기본 연구 경로로 채택하지 않았습니다. 실패한 방법도 채택 근거에 포함합니다. [E4, E5]

로컬 기능 시험은 `KEEP` 9건, `SCALE_OUT` 3건, `SCALE_IN` 3건이며, 오래된 Revision·무효 CPU 관측·오래된 관측 각 3건을 거부했습니다. 회차별 실제 복제본은 1 → 2 → 1 → 0이며 명세는 Revision 1 → 2 → 3으로 이어졌습니다. 복제본 상한 2에서 고부하 p95가 약 90~96ms로 50ms 목표를 넘었지만 추가 증가는 허용되지 않아 `KEEP`을 반환했습니다. 따라서 Action과 적용 연결은 확인했으나 SLO 회복을 달성한 실험이라고 표현하지 않습니다. [E3]

초기 2026-10-07의 7ms 상한은 확장 경로를 확인하기 위한 연결 시험입니다. 2026-10-13 반복 검증은 50ms 상한과 같은 Client HTTP 경계를 사용한 별도 규약입니다. 두 시험의 지표와 상한을 혼합하거나, 서로 다른 연구 버전의 속도비를 곱해 누적 개선률을 만들지 않습니다.

## 6 호환 Planner의 이중 Go Guard 정책

이 절은 기존 자연어 Planner 경로의 정책입니다. 정식 Profile·추천 입력의 배포 및 운영 Guard는 2·3절의 경계와 5절의 평가 조건을 따릅니다.

### 6.1 Go Request Guard

1. 자연어 요구, App Version, LLM candidate 필수값 확인
2. 요청 길이와 요청자 allowlist 확인
3. 1차년도 VM 범위를 벗어나는 Kubernetes·컨테이너·shell 실행 요청 거부
4. password, token, credential, private key, API key 유사 파라미터 거부

### 6.2 Go Manifest Guard

1. `schema_version`과 `kind` 고정
2. 요청의 `app_version_id`와 선택적 Target hint 변조 방지
3. CPU/GPU 정수와 memory/storage 단위 검증
4. accelerator와 GPU 개수 조합 검증
5. 알 수 없는 필드와 복수 JSON 객체 거부
6. password, token, credential, private key, API key 유사 파라미터 거부

검증 실패 시 AppDeploy API를 호출하지 않습니다. LLM 오류를 규칙 기반 성공 결과로 대체하지 않습니다.

## 7 상태 확인과 재시도

AppDeploy 상태는 다음 순서로 조회합니다.

```text
REQUESTED -> VALIDATING -> VALIDATED -> SCHEDULING -> DEPLOYING -> RUNNING
```

실패 상태는 `VALIDATION_FAILED`, `SCHEDULING_FAILED`, `DEPLOYMENT_FAILED`, `RUNTIME_FAILED`, `EXTERNAL_API_FAILED` 등으로 보존합니다. `retryable=true`가 명시된 오류만 재시도 가능으로 판단하고, 배포 생성 POST는 자동 반복하지 않습니다.

## 8 구현 위치

| 기능 | 경로 |
| --- | --- |
| 정식 판단 입력과 API | `README.md`, `docs/openapi_deployment_agent.yaml` |
| 승인 명세와 실제 실행 경계 | `docs/coordination/geon-appdeploy-integration.md` |
| 요청 Guard·정책 | `go/service-control-api/internal/plannerguard/`, `config/planner_guard_policy.json` |
| Manifest 모델·Guard | `go/service-control-api/internal/appdeploy/` |
| LLM 생성·polling orchestration | `go/service-control-api/internal/deploymentplanner/` |
| Agent Registry·Dispatcher | `go/service-control-api/internal/api/agent_registry_runtime.go`, `agent_dispatcher.go` |
| Manifest ControlRun API | `POST /api/v1/control-runs`, `POST /api/v1/agents/{name}/execute` |
| Manifest와 AppDeploy 호환 API | `POST /api/v1/planner/deployments` |
| CLI | `run-appdeploy-planner` |
| 계약 snapshot | `contracts/appdeploy/deployment_manifest.schema.json` |
| 요청·응답 예시 | `examples/requests/run-appdeploy-planner.json`, `examples/responses/run-appdeploy-planner-success.json` |

연구 계산 경로는 상위 통합 작업공간의 `research/scripts/assess_gpu_shape_microbatch_v7.py`와 `row_partition_forest_predict.py`입니다. 연구 결과는 `research/artifacts/row_partition_forest_confirmation_v1/`에 있습니다. 이 경로는 deployment-agent 저장소 단독 배포에 포함된 것으로 가정하지 않습니다. 공유 시 고정 모델·데이터·의존성과 해시를 함께 확인해야 합니다.

## 9 호환 Planner 실행 예시

아래 명령은 기존 Planner CLI의 예시이며 정식 upstream 입력 서버 경로의 실행 안내를 대체하지 않습니다.

```bash
cd go/service-control-api

go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1
```

`MANIFEST_APPROVED` 결과에는 LLM endpoint만 필요하며 AppDeploy 서버는 필요하지 않습니다. 실제 `DEPLOYED` 결과까지 얻으려면 AppDeploy 서버가 실행 중이고 App Version 및 Target Profile이 AppDeploy에 등록되어 있어야 합니다.

## 10 범위와 남은 검증

- 현재 구현은 Go 기반 연구 prototype입니다.
- AppDeploy가 선택한 Target과 배포 상태를 반환하며 플래너가 가상 VM 후보를 만들지 않습니다.
- 정식 핵심 판단 Agent는 `AIApplicationAutomationAgent`와 `OperationOptimizationAgent` 두 개입니다. 기존 Planner CLI의 내장 배포 Agent와 구분하며, Job Scheduling Agent는 포함하지 않습니다.
- 외부 Runtime Agent는 등록된 endpoint에 대한 bounded HTTP 요청 한 건으로만 실행하고 범용 multi-agent workflow는 수행하지 않습니다.
- 실제 운영 인증, TLS, 영속 메시지 큐와 자동 재배포 정책은 후속 통합 항목입니다.
- 1차년도 범위는 VM 기반이며 Kubernetes 배포를 산출물로 주장하지 않습니다.

현재 근거는 전략 설계, 연구용 계산 최적화, 공개 자료의 위험 신호 평가, 로컬 실행·변경 연결 시험입니다. 실제 CPU/GPU 클라우드 VM에서 응용 배포·추론·모니터링까지 성공한 검증은 남아 있습니다. 로컬 프로세스 수를 실제 VM 수로 표현하지 않습니다.

다음 검증에서는 연구 계산 경로를 정식 판단 서비스에 연결할지 먼저 결정하고, 지원 범위·동시 요청·라이브러리 버전과 실패 복귀를 API 단위로 다시 평가해야 합니다. 실제 실행 측과는 action_id, 준비 완료 수·시각과 후속 SLO 결과를 연결해야 합니다. 이후 같은 입력과 부하에서 기준 정책 대비 SLO·자원·비용을 평가합니다. 연결 전의 오프라인 개선치를 플랫폼 성능 향상으로 보고하지 않습니다.

본 개정은 추론 최적화 전략의 목표·방법·피드백·평가·근거를 보완한 것입니다. 공식 과제 항목의 완료 판정은 기관의 산출물 검토 기준에 따르며, 본 문서만으로 1차년도 전체 완료나 실제 클라우드 검증 완료를 선언하지 않습니다.

## 11 근거와 재현 기록

아래 경로는 통합 작업공간 루트 `ai-mcmp` 기준입니다. E3의 로컬 실행 기록과 E4~E6의 연구 기록은 서로 다른 실행 환경과 평가 단위입니다.

- E1 정식 입력과 두 판단 Agent: `deployment_agent/README.md`, `deployment_agent/AGENTS.md`.
- E2 명세 생성과 명시적 전송 경계: `deployment_agent/docs/coordination/geon-appdeploy-integration.md`, 특히 승인된 Revision 2의 자동 배포 금지 설명.
- E3 로컬 반복 기능 검증: `output/khu-platform-validation/workshop-load-validation-20261013/protocol.json`, `verification-summary.json`, `evaluation-2026-10-13T09-42-23.561Z/result.json`. 결과 SHA-256: `c7dbb92ffecf9c0ec7c5b2a357afb4d8b2635bfd3d29868c9ab21cb2c4b6ebd9`.
- E4 계산 최적화 보고서: `research/reports/decision_inference_row_partition_findings_2026_10_05.md`. 검증 재사용의 별도 비교: `research/reports/decision_inference_microbatch_v4_findings.md`.
- E5 계산 최적화 원시 결과와 사전 기준: `research/artifacts/row_partition_forest_confirmation_v1/results.json`, `PROTOCOL.json`, `ADOPTION.json`. 고정 조건은 v6 대비 v7, 2 workers, 256격자, sklearn 1.6.1이며 저장 모델은 변경하지 않았습니다.
- E6 피드백 위험 보정: `research/reports/philly_day_ahead_calibration_holdout_findings.md`, `research/artifacts/philly_day_ahead_calibration_holdout_v1/result.json`. 강한 기준선의 사후 비교 한계도 해당 보고서에 남아 있습니다.
- E7 실제 제어 효과의 연구 데이터 계약: `research/reports/scale_effect_data_contract_v1.md`. 기존 API에 대한 확정 변경안이 아닌 협의용 초안입니다.
- E8 연구 신호와 정식 판단 입력의 제한: `research/reports/khu_research_progress_external_data.md`. 관측 가용량·가격·출처가 없는 shadow 입력은 실행 승인 근거로 사용하지 않습니다.

재현할 때는 원래 결과 디렉터리를 덮어쓰지 않고 새 출력 경로를 사용합니다. 모델·코드·데이터 해시와 의존성 버전을 먼저 확인한 뒤 E4의 benchmark 및 confirmation 명령을 따릅니다. 로컬 실행 시험은 E3의 모델 revision, 50ms 상한, 3회차·5단계 규약을 유지해야 합니다. 이번 개정의 검산 기록과 개정 전 파일은 `output/strategy-supplement-20261014/`에 보존합니다.
