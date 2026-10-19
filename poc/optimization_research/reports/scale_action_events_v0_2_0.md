# 새 VM Scale-out 사건 연결용 연구 계약 0.2.0

이 계약은 [0.1.0](scale_action_events_v0_1_0.md)을 변경하지 않는다. 0.1.0은 전후 사용자 요청이 **같은 `deployment_id`**에 남는 경우를 다룬다. 0.2.0은 기존 배포에 새 VM 배포 하나를 추가하는 **`SCALE_OUT`, 1회에 replica 1개 증가, 배포 1개당 replica 1개**만 다룬다. `SCALE_IN`, 업데이트, 여러 replica를 한 배포에서 운영하는 형태는 별도 버전이 필요하다. 이 문서는 연구 데이터 수집 형식이며 실행 API가 아니다.

[JSON Schema](../contracts/scale_action_events_v0.2.0.schema.json)는 JSONL 각 행의 형식을, [검증기](../scripts/validate_scale_action_events_v0_2.py)는 행 간 식별자·시각·라우팅 연결을 검사한다. `logical_service_id`는 여러 배포가 함께 서빙하는 **안정적인 서비스 식별자**다. `app_id`만으로 여러 독립 서비스를 합치거나 새 배포 ID를 기존 배포 ID로 바꾸어 쓰지 않는다. 모든 사건은 앱 ID·버전·서비스 ID, 원본 사건 시각 `observed_at`, 경희대 수집 가능 시각 `available_at`, 시각 출처를 보존한다.

## 수집할 사건

| 사건 | 필수 연결과 의미 |
| --- | --- |
| `DECISION` | 경희대 `decision_id`, `correlation_id`, 정책 버전, 판단 전 활성 배포 ID 목록·라우팅 버전, 현재·목표 서비스 전체 replica 수, 판단 전에 사용할 수 있었던 마지막 입력 시각, 사전에 정한 전후 평가 창. `KEEP` 등은 이 좁은 버전의 대상이 아니다. |
| `ACTION_ISSUED` | **실행 측 접수 확인**으로 발급한 고유 `action_id`, `decision_id`, revision 요청 ID, 시도 번호, 접수 상태. 경희대의 Manifest 요청 ID나 HTTP POST 시작은 이 사건이 아니다. 새 배포 ID는 접수 시 확인됐다면 기록하고, 아직 없으면 후속 결과에서 확인한다. |
| `ACTION_STATUS` | 선택적 `RUNNING` 등 중간 상태. 실제 준비 완료의 증거가 아니며 `ACTION_RESULT`를 대체하지 않는다. |
| `ACTION_RESULT` | 같은 `action_id`의 `READY` 또는 `FAILED`. `READY`는 새 배포 ID, 새 배포의 준비 replica 수 1, **서비스 전체** 준비 replica 수, 서비스 건강을 원본에서 함께 계측해야 한다. `RUNNING` 문자열이나 상태만 있는 `STATUS_ONLY`는 연결 적격이 아니다. |
| `TRAFFIC_HANDOFF` | 같은 `action_id`, 새 배포 ID, 전환 전후 라우팅 버전·활성 배포 ID 집합. `observed_at`은 라우터가 실제 전환을 적용한 원본 시각이다. `traffic_handoff_required=true`나 예정 시각은 해당하지 않는다. |
| `REQUEST_RESULT` | 사용자 요청 ID, 실제 처리한 `served_deployment_id`, 라우팅 버전, 시작·완료 시각, 성공 여부, 종단간 지연, 요청 종류. 배포 명령의 요청 ID와 다른 이름 공간이다. |

## 연결 조건

1. 판단 입력의 `inputs_max_available_at ≤ DECISION.observed_at`이고, 실행 측 접수 시각은 판단보다 늦다. `action_id`는 재시도마다 새로 발급한다. 미확인(`UNKNOWN`) 접수 뒤 무증거 재시도, 중복 `READY`, 미종료 접수 시도는 보류한다.
2. 같은 앱·버전·서비스·`correlation_id`의 `ACTION_ISSUED → ACTION_RESULT(READY) → TRAFFIC_HANDOFF`를 `action_id`로 연결한다. 세 사건의 원본 시각은 **접수 < 준비 완료 ≤ 전환** 순서다. 로컬 관측 대체 시각이나 합성 시각은 적격이 아니다.
3. 새 배포 ID는 판단 전 활성 집합에 없어야 한다. 전환 전 집합은 판단·명령의 집합과 같고, 전환 후 집합은 **전환 전 집합 ∪ 새 배포 ID**와 정확히 같아야 한다. 서비스 전체 준비 replica 수는 목표 수와, 새 배포 준비 replica 수는 1과 같아야 한다. 전환 후 라우팅 버전은 전환 전과 달라야 한다.
4. 사전 요청은 정해 둔 창에서 판단 전에 완료되고 **그때까지 수집돼 사용 가능**해야 하며, 이전 라우팅 버전의 이전 활성 배포에서 처리돼야 한다. 사후 요청은 실제 전환 **이후 시작**해 정해 둔 창에서 완료되고, 새 라우팅 버전의 전환 후 활성 배포에서 처리돼야 한다. 양쪽에 공통 요청 종류가 있어야 하며, 새 배포가 처리한 사후 요청 중에도 그 종류가 하나 이상 있어야 한다. 요청 종단간 지연은 `completed_at−created_at`과 최대 1ms 반올림 차이만 허용한다.
5. 다른 접수·미확인 조치나 라우팅 전환이 같은 서비스의 **사전에 정한 전체 평가 창**에 걸치면 보류한다. 마지막으로 관측된 사후 요청에서 검사를 끝내지 않는다. 평가 창 전에 종료된 다른 조치도 그 종료 사건 **뒤에 같은 조치의 상태가 다시 기록되면** 종료가 확실한 이전 조치로 인정하지 않는다. `REJECTED` 조치에 실행 상태·결과·전환 기록이 붙거나, 접수 기록이 없는 실행 상태·결과가 창 안에 나타나도 보류한다. 상태의 서비스·대상 배포 ID가 연결된 명령과 후속 결과에 맞지 않거나 상태 시각이 명령 접수보다 앞서도 보류한다. 다른 앱 버전의 요청이 같은 서비스 창에 나타나도 보류한다. 같은 ID의 동일 재전송은 한 번으로 취급하고, 정정은 원본을 덮어쓰지 않는 새 사건 ID와 증가한 버전으로만 허용한다. `--as-of`는 당시 수집 가능했던 버전만 선택하고, 지정한 시점이 사후 창 종료 전이면 연결 적격으로 판단하지 않는다.

`linked_outcome_eligible=true`는 **제공된 원본 시각·식별자 선언을 기준으로 한 관측 연결**만 뜻한다. 한 창에 요청이 1건씩 있어도 구조적 연결은 될 수 있으므로 이 플래그만으로 p95, 효과 크기 또는 SLO 달성률을 계산하지 않는다. `source_authenticity_verified=false`는 검증기가 전송자 신원, 서비스 건강 계측, 실제 사용자 트래픽 여부를 확인하지 못한다는 뜻이다. `event_stream_completeness_verified=false`는 제공된 JSONL 밖에 미수집 조치·요청이 없는지 증명하는 수집기 워터마크가 아직 계약에 없다는 뜻이다. 원본 제공 기관의 로그·인증된 전송 경로와 완전성 기록으로 별도 확인해야 한다. 비교군 배정과 동시 수요·비용·다른 자원 변화가 없는 이 버전의 `causal_effect_eligible`은 **항상 `false`**다.

현재 AgentControl의 revision 2 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/agentcontrol/service.go`)는 스케일 **제안**이다. Go AppDeploy 연계 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/api/flow_delivery.go`)는 최초 revision 1만 제출한다. 별도 자율 실행기 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/autonomy/executor.go`)는 새 배포를 요청할 수 있지만 `ExecutionSucceeded`는 트래픽 전환·준비 완료·사용자 SLO 결과가 아니다. 이 값들을 `ACTION_ISSUED`, `READY`, `TRAFFIC_HANDOFF`로 자동 변환하지 않는다. 실행 시스템과 요청 게이트웨이가 각각 원본 사건을 제공해야 한다.

## 검증

```bash
python3 poc/optimization_research/scripts/validate_scale_action_events_v0_2.py \
  --input poc/optimization_research/examples/scale_action_events_v0.2.0.example.jsonl
python3 -m unittest discover -s poc/optimization_research/tests -p 'test_validate_scale_action_events_v0_2.py' -v
```

[예시 JSONL](../examples/scale_action_events_v0.2.0.example.jsonl)의 모든 행은 `SYNTHETIC`이다. 형태가 완전해도 연결 적격은 `false`이며 실제 배포·효과의 증거가 아니다.

반례 회귀 검사에서는 거절 시도의 `RUNNING` 상태, 이전 조치의 `FAILED` 뒤 현재 평가 창에 나타난 `RUNNING` 상태, 마지막 사후 요청 뒤이지만 고정 평가 창 안에 있는 다른 조치, 다른 서비스의 action ID를 가리키는 상태, 상태와 `READY`의 배포 ID 불일치, 명령보다 앞선 상태 시각, 사후 창 종료 전 `--as-of`를 각각 연결 부적격으로 확인한다. [집중 테스트](../tests/test_validate_scale_action_events_v0_2.py)는 이 사례와 0.1.0과의 분리를 검사한다.
