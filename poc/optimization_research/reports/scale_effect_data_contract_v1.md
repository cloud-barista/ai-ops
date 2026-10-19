# 실제 배포·스케일 판단 연구를 위한 데이터 계약 초안

2026-09-28 기준 기관 간 협의용 **연구 데이터 계약**이다. 플랫폼 API나 기존 `Deployment Spec`의 확정 변경안은 아니다. 목적은 경희대 판단기의 입력과 실행 후 결과를 동일한 사건으로 연결해, 부하 위험 예측을 실제 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 판단 평가로 확장하는 것이다.

## 현재 계약과 부족한 연결

현재 Resource Ops 계약 (`../../resource_ops/docs/contracts.md`)은 `ApplicationProfile`을 받아 자원 후보를 추천하며 `request_id`, `profile_id`, 후보 `resource_id`, `observed_at`을 제공한다. Deployment Agent 계약 (`../../deployment_agent/docs/openapi_deployment_agent.yaml`)은 같은 `correlation_id=request_id`와 `profile_id`의 일치를 검사한다. 배포 연계 경계 (`../../deployment_agent/docs/design/integration_boundary.md`)는 실행을 AppDeploy, 자원 조회를 Resource Ops에 둔다. 이 식별자들은 연구 로그의 연결 키로 재사용할 수 있다.

현재 공개 [GenTD26 요청 자료](gentd26_request_latency_findings.md)에는 요청 생성 시각과 실행시간만 있고 실제 완료·결과 수집 시각이 없다. [모니터링 자료](gentd26_monitoring_findings.md)에는 부하·대기열은 있지만 VM 수 변화와 명령 결과가 없다. 기존 [SQLite 감사](untapped_signal_audit.md)의 scale 라벨은 정적 복제 측정에서 파생돼 실제 전환 정답이 아니다. 다음 표의 사건들을 함께 기록해야 이 간극이 좁혀진다.

### 현재 PoC에서 이미 기록되는 것과 빠진 것

2026-09-29 코드 점검 기준, Go Flow에는 최초 `decision_id`, `deployment_id`, 상태와 집계 피드백이 있다. 후속 승인 `ScalingDecision`에도 경희대가 부여한 별도 `decision_id`가 있고, 최적화 revision의 `trigger_decision_id`가 이를 가리킨다. 그러나 이는 실행 측이 확인한 조치별 `action_id`가 아니다. 선택형 경희대 스케일 판단 저널 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/agentcontrol/scaling_decision_journal.go`)은 승인 판단의 계획 수량·입력 해시·수신 시각을 제한된 스냅샷으로 로컬 보존하지만 실행 사건은 기록하지 않는다. AppDeploy 연계 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/api/flow_delivery.go`)는 최초 revision의 로컬 요청 ID, 제출 예약·응답 시각과 AppDeploy 상태 원본 시각·조회 시각을 delivery journal에 보존한다. 이 기록의 요청 ID는 AppDeploy가 되돌려 준 실행 조치 ID가 아니며, 상태 관측은 준비된 replica 수나 서비스 건강 상태를 포함하지 않는다. 자율 실행 기록 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/autonomy/manager.go`)은 최근 200개를 메모리에 유지하고, scale 요청의 접수는 응용 트래픽 전환이나 준비 완료를 증명하지 않는다. 따라서 현재 Flow와 저널만으로 명령별 전후 사용자 SLO 결과를 재구성할 수 없다.

상태의 원본 `updated_at`과 경희대 조회 시각은 구분한다. 전자는 AppDeploy가 선언한 상태 갱신 시각, 후자는 경희대가 조회한 시각이다. `fetched_at`은 저널 저장 전에 찍히므로 연구 계약의 `available_at`으로 자동 대응하지 않고 실제 수집기 가용 시각을 별도로 기록해야 한다. 둘 다 연구 계약의 실제 서비스 `READY` 사건이나 후속 사용자 결과로 자동 변환할 수 없다. 원본 시각이 없으면 로컬 조회 시각을 사용했다는 표시가 남지만, 이를 AppDeploy의 실제 RUNNING 전이 시각이라고 간주해서는 안 된다. 현재 Scaling Guard가 확인하는 최신 명세 요청 ID에 대한 상태 참조도 **적용된 replica 수의 독립 계측이 아니다.** 이노그리드·ETRI 연동에서 revision별 명령 ID와 실제 준비 완료 수·시각, 후속 사용자 결과를 별도 사건으로 수집해야 한다.

## 최소 사건 기록

모든 시각은 timezone을 포함한 RFC 3339 형식으로 기록하고, `observed_at`(현상이 발생·계측된 시각)과 `available_at`(판단기가 값을 사용할 수 있게 된 시각)을 구분한다. 지연된 계측값을 과거 시점에 이미 알려진 값으로 취급하지 않기 위해서다. `request_id`와 `correlation_id`는 현재 계약과 호환하고, `action_id`·`deployment_id`는 실행 흐름에 연결한다.

| 사건 | 필수 식별자·시각 | 필요한 값 | 주 제공 주체(잠정) |
| --- | --- | --- | --- |
| 응용 요구와 SLO 버전 | `profile_id`, `app_id`, `app_version`, `effective_at`, `available_at` | 작업 종류, 입력 크기/토큰 또는 이미지 단계, 목표 p95 지연·처리량, 허용 비용, 등록 모델 크기와 출처 | 이노그리드 |
| 사용자 요청 결과 | `request_id`, `app_id`, `app_version`, `created_at`, `completed_at`, `available_at` | 성공/실패, end-to-end 지연, 필요한 경우 순수 실행시간, 요청 복잡도 요약. 민감한 프롬프트 원문은 요구하지 않는다 | 이노그리드 |
| 모니터링 표본 | `app_id`, `deployment_id`, `resource_id` 또는 `vm_id`, `observed_at`, `available_at` | 표본 간격, 요청수·대기열·오류율·CPU·RAM·GPU 사용량, 관측 누락 여부와 수집기 생존 신호. 0과 미관측을 구분하고 GPU 메모리의 사용량·할당량·백분율 분모를 함께 기록 | 이노그리드·ETRI |
| 당시 자원·가격 스냅샷 | `resource_id`, `observed_at`, `available_at` | 총/가용 CPU·RAM·GPU와 종류, 리전, 상태, 가격과 가격 유효 시각 | ETRI |
| 경희대 판단 | `decision_id`, `correlation_id`, `profile_id`, `decided_at`, `policy_version` | `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP/ABSTAIN`, 추상 인프라 형상, 후보 점수, 지원 범위와 보류 이유, 사용한 입력의 최대 `available_at` | 경희대 판단 모듈 |
| 실행 명령·상태 | `action_id`, `decision_id`, `deployment_id`, `requested_at`, `finished_at` | 실제 명령 종류, 명령 전/후 요청 replica 수와 실제 준비 완료 수, VM·응용 ready 시각, 실패 사유 | 이노그리드·ETRI |

경희대 판단 결과의 **추상 형상**은 CPU·메모리·GPU 종류/수·노드 수·배치 조건으로 표현한다. 실제 CSP·리전·VM ID·Manifest는 실행 측에서 결정·기록한다. 기존 Resource Ops의 `resource_id`는 이미 있는 후보를 가리키며, 아직 생성되지 않은 VM의 ID로 사용하지 않는다.

### Shadow 입력의 출처와 지원 범위

현재 shadow 연동 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/api/shadow_policy_api.go`)은 Flow에 아래 원본 필드가 없어 `ABSTAIN`한다. 실제 입력 계약을 정할 때 후보마다 다음을 별도 필드로 보존해야 한다.

- `total_cpu_cores`, `available_cpu_cores`, `total_memory_mib`, `available_memory_mib`, `total_storage_gib`, `available_storage_gib`, GPU별 총/가용 메모리와 장치 건강 상태. 요구 형상의 용량, 후보 적합성(`feasible`), 추천 점수는 가용량·건강 상태의 대리값이 아니다.
- 가격의 숫자·통화·`per_hour` 기준·유효 시작/종료 시각·원본 제공자. `1 - cost_efficiency` 같은 변환 점수는 가격이 아니다. 가격이 없는 후보는 결측으로 보존한다.
- 각 수치의 `resource_id`, `source`, `observed_at`, `available_at`, 단위, 계측/추정 여부. 여러 노드 후보는 노드별 값을 남기고, 추상 노드 수와 실제 관측 노드를 구분한다.

입력 시점과 단위가 맞고 필수 특성 모두에 검증 가능한 출처가 있으며, 학습 모델의 응용·형상·행동 라벨 지원 범위에 들어올 때만 shadow 점수를 계산한다. 하나라도 부족하면 `ABSTAIN`의 이유를 기록한다. 이 계약은 **연구용 비교의 재개 조건**이며, 실제 배포·스케일 명령 승인 조건은 별도 검증이 필요하다.

## 수집·학습 품질 게이트

1. 입력 특징은 판단 시각보다 늦게 가용해진 값을 포함하지 않는다: 모든 사용 표본의 `available_at ≤ decided_at`. 결과 레코드도 실제 `completed_at ≤ available_at` 순서를 지킨다.
2. 하나의 `decision_id`가 여러 재시도를 낳으면 각 `action_id`와 성공·실패를 모두 남긴다. 명령 요청을 VM 준비 완료나 서비스 배포 성공으로 간주하지 않는다. 실행된 명령이 없는 `KEEP/ABSTAIN`도 기록한다.
3. `SCALE_OUT`의 품질은 실제 준비 완료 뒤의 요청 지연·처리량·오류·비용을 사전 정의한 시간 창에서 평가한다. 큐 위험 예측, 기존 정책이 내린 명령, 생성한 SLO 쌍을 “증설 필요”의 정답으로 자동 치환하지 않는다.
4. 명령 전후 비교에는 같은 응용의 트래픽, 요청 복잡도, GPU 형상과 동시 배포 변경을 기록한다. 가능한 경우 비개입 비교 구간 또는 사전에 정한 정책 실험군을 남긴다. 비교군이 없으면 **관측 연관성**만 평가한다.
5. 학습·평가는 응용/모델을 통째로 제외하는 분할과 시간순 평가를 각각 보고한다. 같은 배포 사건에서 나온 반복 표본·겹치는 미래 결과 창이 양쪽 분할에 걸치지 않게 한다.
6. 관측 누락은 별도 결측 상태로 보존한다. 예를 들어 QPS 표본이 없다는 사실을 실제 요청 0건으로 바꾸지 않는다. 수집기 생존 신호가 있어야 긴 무기록이 무부하인지 수집 장애인지 판단할 수 있다. 단위도 고정한다: 사용자 지연 ms, 요청/초, GPU 메모리 MiB, 비용/시간. GPU 사용률은 분자·분모와 허용 범위를 버전별로 기록하고 범위 밖 표본의 원인 또는 무효 코드를 남긴다.
7. 상태와 피드백은 수신 순서만으로 최신이라고 판단하지 않는다. 같은 메시지 ID의 재전송은 하나의 사건으로 다루고, 서로 다른 상태 메시지는 원본 상태 시각·명세 revision을, 피드백은 관측 창 종료 시각·생성 시각을 함께 검사한다. `observation_window.ended_at ≤ feedback.created_at ≤ message.occurred_at` 순서를 지키고, 늦게 도착한 과거 관측이 최신 상태를 덮지 않게 한다. 같은 관측 창의 정정값을 허용하려면 원본 사건을 가리키는 별도 정정 ID와 버전 규칙을 먼저 합의한다.

최초 수집 단계에서는 위 사건의 **연결성과 시각 정확성**부터 검증한다. 실제 scale 실행 표본과 비교 구간이 확보되면, 그때 판단 정책의 SLO 달성률·잘못된 승인·비용·준비 지연을 함께 평가한다. 공개 추적 자료와 로컬 SQLite는 이 계약의 초기 특징·평가 코드 검증에 사용하고, 실제 전환 결과의 대체 정답으로 쓰지 않는다.
