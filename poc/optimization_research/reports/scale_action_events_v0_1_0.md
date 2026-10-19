# 배포·스케일 사건 연결용 연구 계약 0.1.0

이 문서는 [연구 데이터 계약 초안](scale_effect_data_contract_v1.md)의 **최소 수집 형식**이다. [JSON Schema](../contracts/scale_action_events_v0.1.0.schema.json)는 JSONL 한 줄의 형식을, [표준 라이브러리 검증기](../scripts/validate_scale_action_events.py)는 여러 줄의 ID·시각·재시도 관계를 확인한다. 기관 간 확정 API가 아니며, 이 JSONL은 별도로 수집한 실제 사건만 담는다.

## 한 판단에 필요한 사건

| 사건 | 연결 키 | 사건 시각과 필수 의미 |
| --- | --- | --- |
| `DECISION` | `decision_id`, `correlation_id`, `profile_id` | `observed_at`은 판단 시각. `inputs_max_available_at`은 판단에 사용한 가장 늦은 입력의 수신 시각이며 판단보다 늦을 수 없다. 평가 창의 길이도 이때 확정한다. |
| `ACTION_ISSUED` | 새 `action_id`, `decision_id`, `revision_request_id`, `attempt_no` | `observed_at`은 실행 측 명령 접수 시각. `dispatch_state`는 `ACCEPTED`, `REJECTED`, `UNKNOWN`을 구분한다. 명세 생성이나 POST 시작만으로 `ACCEPTED`라고 쓰지 않는다. |
| `ACTION_RESULT` | `action_id`, `deployment_id` | `READY`는 실제 서비스 건강 상태와 준비 완료 replica 수를 함께 계측한 때의 시각 및 수량을 요구한다. 단순 `RUNNING` 문자열은 `STATUS_ONLY`로 보존하며 연결 적격이 아니다. `FAILED`는 실패 사유를 남긴다. |
| `REQUEST_RESULT` | 응용·버전·`deployment_id`·사용자 `request_id` | 실제 사용자 요청의 시작·완료, 성공 여부, 지연, 요청 종류를 기록한다. 이 `request_id`는 배포 요청 ID와 다른 이름 공간이다. |

`REQUEST_RESULT.latency_ms`는 이 버전에서 `completed_at−created_at`의 **종단간 요청 경과시간**이다. 원본의 나노초 정밀 시각으로 계산한 값과 최대 1ms의 기록·반올림 차이만 허용한다. 서버 내부 실행시간처럼 다른 구간을 재는 수치는 별도 필드·버전으로 정의해야 한다. 수치만 그럴듯하고 두 시각과 일치하지 않는 요청은 연결 적격 자료로 사용하지 않는다.

모든 사건의 `observed_at`은 **원본 사건 시각**, `available_at`은 경희대 수집기가 그 레코드를 사용할 수 있게 된 **로컬 시각**이다. 원본 시각이 없는 상태 조회는 `time_provenance=LOCAL_OBSERVATION`으로 표시한다. 합성 예시는 `SYNTHETIC`이다. 둘 다 실제 준비 완료나 요청 결과의 원본 시각으로 승격하지 않는다. 모든 시각은 timezone이 있는 RFC 3339 형식이다. 검증기는 선언된 시각 순서를 확인하지만 전송자 인증이나 원본 계측의 진위를 확인하지는 못한다.

## 판정 기준

`linked_outcome_eligible=true`는 다음 **관측 자료 연결**이 완성됐다는 뜻이다. 실행 명령은 판단 뒤에 접수돼야 하며, 접수 확인 뒤 실제 `READY` 사건에 동일한 action ID와 배포 ID가 있어야 한다. 준비 완료 replica 수가 요청 수와 일치해야 한다. `DEPLOY`는 준비 이후 요청 결과가, `UPDATE`와 `SCALE_OUT/IN`은 판단 전에 이미 사용 가능했던 요청 결과와 준비 이후 요청 결과가 같은 배포·응용·버전에서 있어야 한다. 전후 요청 종류에 공통 항목이 있어야 한다. 지정한 전후 시간 창은 판단 사건에 먼저 기록한다. 소스 시각이 아닌 로컬 대체 시각이나 합성 자료는 제외한다.

다른 결정에서 접수된 조치가 같은 응용·버전의 평가 창에 걸치면 연결을 보류한다. 이 계약에는 배포 간 트래픽 분할·격리 키가 없으므로 다른 `deployment_id`의 조치도 포함한다. 평가 창은 `판단 시각 - pre_window_seconds`부터 마지막 사후 요청 완료 시각까지다. 다른 조치가 이 창이 끝나기 전에 발행됐고, `ACTION_RESULT`가 없거나 결과의 원본 시각·상관 ID·배포 ID가 불확실하면 진행 중인 것으로 취급한다. 유효한 `READY` 또는 `FAILED` 결과 시각이 창 시작과 같거나 그 뒤면 조치가 창에 겹친 것으로 취급한다. 유효한 결과가 창 **시작 전**에 있고 발행 시각보다 이르지 않으면 이전에 완료된 조치로 보아 제외한다. 동일한 판단의 명시적 실패 후 재시도는 이 교란 검사에서 제외하고 별도의 재시도 규칙으로 검사한다. 이 판정은 관측 자료의 구조적 연결 조건이며 인과 효과를 증명하지 않는다.

다른 조치의 `READY`가 평가 창 전에 있었다고 해도 `readiness_signal=STATUS_ONLY`이거나 실제 준비 완료 replica 수가 요청 수량과 다르면 종료를 증명하지 못한다. 그런 조치는 **진행 중 또는 불명확한 조치**로 남겨 `overlapping_accepted_action`을 반환한다. 선택된 조치의 준비 완료와 같은 증거 수준을 다른 조치의 교란 종료 판단에도 적용한다.

이 플래그는 **원본의 자기 선언을 기준으로 한 구조적 연결**만 뜻한다. 출력의 `source_authenticity_verified=false`가 이 한계를 명시한다. `READY`의 실제 건강 상태, 요청이 실제 사용자 트래픽인지, 지연의 수집 방식은 제공 기관의 원본 기록과 별도 검증해야 한다. `inputs_max_available_at`도 입력 사건 ID와 대조하는 값이 아니라 제공자의 선언이다. 한 창에 요청 1건만 있어도 연결 자체는 성립할 수 있으므로, 이 플래그만으로 p95·처리량·효과 크기를 계산하거나 성능 주장을 해서는 안 된다.

`causal_effect_eligible`은 **0.1.0에서 항상 `false`**다. 비교군 또는 사전 정책 실험 배정과 전후 동시 부하·요청 복잡도·함께 발생한 배포 변경을 연결하는 합의된 사건이 아직 이 버전에 없다. 이 자료로는 관측 연관성을 살펴볼 수 있어도 증설의 인과적 효과를 학습 정답으로 삼을 수 없다. 이 정보가 수집되면 별도 버전을 정의하고 검증 조건을 추가해야 한다.

## 중복·재시도·정정

- 같은 `event_id`의 동일 재전송은 한 번만 읽는다. 같은 ID로 다른 내용이 오거나 JSON 객체에 같은 키가 중복되면 오류다.
- 새 명령 시도는 새 `action_id`와 증가한 `attempt_no`를 쓴다. 두 번째 시도부터 `retry_of_action_id`로 바로 전 시도를 참조하고 같은 명세 revision·요청 ID를 유지한다. 앞 시도가 명시적으로 `REJECTED`이거나 `FAILED`로 종료된 뒤에야 연결 적격이 된다. `UNKNOWN` 접수 후 조용히 재시도하면 보류한다.
- 여러 `ACCEPTED` 시도가 있을 때 다른 시도가 새 명령 전에 명시적으로 실패하지 않았다면 보류한다. 다른 배포 ID나 빈 배포 ID로 모호함을 숨길 수 없다.
- 정정은 원본을 덮어쓰지 않고 새 `event_id`, `correction_of`, 한 단계 증가한 `correction_version`을 넣는다. 사건 종류·논리 ID·응용·버전·출처는 유지하고 `available_at`은 이전 버전보다 이르지 않아야 한다. `--as-of`는 그 시점에 사용 가능했던 최신 정정만 적용한다. 분기 정정이나 정정 이전에 가용했던 것처럼 꾸민 기록은 거부한다.

## 현재 Go PoC와의 경계

현재 Go Flow (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/agentcontrol/models.go`)의 승인된 `ScalingDecision`에는 피드백별로 재현 가능한 `decision_id`가 붙고, 최적화 `ManifestRevisions[]`는 `trigger_decision_id`로 해당 판단을 가리킨다. 이는 **판단 출처 ID**이며 명령 `action_id`나 실행 측 접수 확인이 아니다. `ScalingDecisionHistory[]`는 Guard 승인된 `KEEP`·`SCALE_OUT`·`SCALE_IN` 판단과 당시 응용 프로필·자원 추천·배포 상태·최적화 피드백의 입력 복사본, 각 입력의 KHU 수신 시각, KHU 판단 확정 시각을 최근 128건까지 보존한다. `KEEP`는 최적화 revision 없이 기록되며, 모든 승인 판단은 최신 Manifest 요청을 가리키는 상태와 그 상태에 연결된 피드백을 요구한다. `current_replicas`는 이 계획의 희망 수량이지 실제 적용 수량의 독립 계측값이 아니다. 초과 이력은 `scaling_decision_history_dropped`로 세지만, 피드백 메시지 ID의 재사용을 막는 해시 기록은 해당 Flow가 살아 있는 동안 별도로 유지한다. 이 두 기록은 메모리에만 있으며 Flow 삭제·초기화나 서버 재시작 시 사라진다. 선택형 경희대 스케일 판단 저널 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/agentcontrol/scaling_decision_journal.go`)을 설정하면 승인 판단별 입력 해시·계획 수량·수신 시각·원본 집계 피드백의 제한된 스냅샷을 로컬 파일로 보존한다. 저널은 Flow의 과거 입력 전체나 실행 결과를 복원하지 않으며, 파일 게시 직후 중단되면 Flow 승인이나 HTTP 성공 없이 파일만 남을 수 있다. 상류 `occurred_at`·`updated_at`은 제공자의 자기 선언이고, KHU 지역 수신 시각은 해당 시스템의 실제 사건 시각이 아니다. 수신 시각 네 개가 있어도 초기 계획 산출물의 사용 가능 시각과 연구 계약의 입력 사건별 연결·판단 관측 시각을 검증하지 못하므로 `inputs_max_available_at`을 자동 확정할 수 없다. 고정된 평가 창, 정책 버전, 명령 접수 및 사용자 결과도 이 Flow와 저널만으로 증명할 수 없다.

AppDeploy 연계 (`/home/researcher/ai-mcmp/deployment_agent/go/service-control-api/internal/api/flow_delivery.go`)는 초기 revision 제출과 상태 조회를 다루며, `RUNNING` 상태나 요청 접수만으로 준비된 replica 수를 독립 계측하지 않는다. 상태 이력의 `source_updated_at`은 AppDeploy가 선언한 **상태** 변경 시각이고 `fetched_at`은 KHU 조회 시각이다. 조회 시각은 journal 저장보다 이르므로 연구 입력의 `available_at`으로도 자동 복사하지 않는다. `effective_updated_at`이 로컬 대체 시각일 때는 실제 전이 시각이 더더욱 아니다. AppDeploy의 지표 레코드에도 사용자 요청 단위의 완료·지연과 p95 산출 창이 연결돼 있지 않다. 따라서 **Go Flow, delivery receipt, 상태·집계 피드백을 이 JSONL의 `ACTION_ISSUED`·`ACTION_RESULT`·`REQUEST_RESULT`로 자동 변환하지 않는다.** 이노그리드·ETRI가 조치별 식별자, 실제 준비 수량, 사용자 요청 결과를 제공한 뒤 별도 수집기를 합의해야 한다.

## 실행

```bash
python3 poc/optimization_research/scripts/validate_scale_action_events.py \
  --input poc/optimization_research/examples/scale_action_events_v0.1.0.example.jsonl
python3 -m unittest research.tests.test_validate_scale_action_events -v
```

[작은 JSONL 예시](../examples/scale_action_events_v0.1.0.example.jsonl)는 모든 행이 `SYNTHETIC`이므로 연결 적격이 `false`로 나온다. 실제 수집 파이프라인에서는 원본 시각·로컬 수신 시각과 고유 ID를 사건이 들어올 때 저장해야 한다. 지나간 Flow 스냅샷으로 이를 소급 생성할 수 없다.
