# geon Autonomous Loop Design

## 목적

geon Agent Control에 사용자의 개별 요청 없이 AppDeploy 상태와 추론 지표를 주기적으로 감시하고, SLO 위반을 확인한 뒤 Qwen, Agent Registry, Go Guard를 거쳐 허용된 복구 Action을 자동 실행하는 폐루프 제어 기능을 추가한다.

이 기능은 AppDeploy 소스를 수정하지 않는다. geon은 AppDeploy REST API를 사용하는 제어 평면이고, AppDeploy는 App/Target 관리와 실제 배포 실행을 담당한다.

## 범위

### 포함

- geon Agent Control의 `Autonomous Loop` 웹 화면
- `Monitor Only`와 `Guarded Auto` 실행 모드
- AppDeploy deployment, metrics, monitoring API 주기적 조회
- 지연시간, 처리량, 오류율 SLO 평가
- 연속 위반 횟수, Cooldown, 최대 실행 횟수 정책
- Qwen 기반 복구 Action 제안
- Agent Registry와 Go Guard 검증
- AppDeploy를 통한 상태 조회, 중지, 재시작, Rollback, 추가 Deployment 실행
- 실행 후 새 지표를 이용한 결과 재평가
- 백엔드 이벤트 Timeline과 웹 표시
- Emergency Stop

### 제외

- AppDeploy 소스 변경
- Kubernetes, Container, Load Balancer 제어
- 추가 Deployment로 전달되는 트래픽의 자동 분산
- Credential 입력 또는 저장
- Standby Target 자동 생성
- 이전 App Version 자동 추측
- 서버 재시작 후 `Guarded Auto` 자동 재활성화

## 실행 모드

### Monitor Only

- 모니터링 루프와 SLO 평가를 실행한다.
- Qwen Action 제안과 Go Guard 검증까지 실행한다.
- AppDeploy 상태 변경 API는 호출하지 않는다.
- 결과를 `would_execute` 이벤트로 기록한다.

### Guarded Auto

- Monitor Only의 전체 판단 단계를 실행한다.
- 연속 위반, 증거 신선도, Cooldown, 실행 횟수, Action별 필수 입력 조건을 통과한 경우 AppDeploy Action을 실행한다.
- 실행 후 Cooldown에 진입하고 새로운 지표가 수집될 때까지 추가 Action을 실행하지 않는다.

기본 모드는 `Monitor Only`다. 서버 재시작 시 실행 모드는 항상 `Monitor Only`, 루프 상태는 `stopped`로 초기화한다.

## 아키텍처

```text
geon Agent Control Web
        |
        v
Autonomy REST API
        |
        v
Autonomy Manager -----> Event Store
        |
        +-----> AppDeploy Monitoring Client
        |       - deployment status
        |       - metrics
        |       - alarms/runtime health
        |
        +-----> SLO Evaluator
        |
        +-----> Qwen Action Planner
        |
        +-----> Agent Registry + Go Guard
        |
        +-----> AppDeploy Action Executor
                - observe
                - stop
                - restart
                - rollback
                - additional deployment
```

### Autonomy Manager

- 설정과 현재 상태를 동시성 안전하게 보관한다.
- 시작 시 하나의 goroutine과 ticker만 생성한다.
- 중복 시작 요청은 기존 루프 상태를 반환한다.
- 중지와 Emergency Stop은 context cancellation로 진행 중인 주기를 취소한다.
- 한 주기에는 감시 대상 Deployment당 최대 하나의 Action만 실행한다.
- 최근 200개 이벤트를 메모리에 유지한다.

### AppDeploy Monitoring Client

기존 `internal/appdeploy.Client`를 다음 계약으로 확장한다.

```text
GET  /deployments
GET  /deployments/{deployment_id}
GET  /deployments/{deployment_id}/metrics
GET  /monitoring/summary
POST /deployments/{deployment_id}/stop
POST /deployments
```

AppDeploy base URL은 기존 `AIOPS_APPDEPLOY_BASE_URL`을 사용한다.

### SLO Evaluator

최신 Metric을 대상으로 다음 조건을 평가한다.

- `latency_ms > max_latency_ms`
- `throughput_rps < min_throughput_rps`
- `error_count / request_count > max_error_rate`
- Deployment 상태가 실패 상태
- Monitoring summary 또는 Runtime Health가 degraded/unavailable

Metric timestamp가 `max_metric_age_seconds`보다 오래됐거나 Metric이 없으면 `insufficient_evidence`로 판정하고 상태 변경 Action을 실행하지 않는다.

각 위반은 Deployment별로 누적한다. 모든 SLO가 정상으로 돌아오면 연속 위반 횟수를 0으로 초기화한다. `consecutive_violations`에 도달하기 전에는 `observing_violation` 이벤트만 기록한다.

### Qwen Action Planner

확정된 SLO 위반과 현재 Deployment 정보를 기존 Qwen automation planner에 전달한다. 허용 Action은 다음으로 제한한다.

```text
observe_status
scale_out_application
restart_application
rollback_application
stop_application
```

Qwen 응답은 기존 strict JSON 파싱을 사용한다. 허용 목록 밖의 Action, 다른 Deployment ID, Credential 형태의 값, 실행 결과를 가장한 응답은 거부한다.

### Go Guard와 실행 Gate

Qwen 제안 후 다음 조건을 결정론적으로 검증한다.

- Agent Registry에 Action이 등록되어 있다.
- SLO가 설정된 횟수만큼 연속 위반했다.
- 최신 Metric 또는 실패 상태 증거가 있다.
- Deployment가 Cooldown 상태가 아니다.
- Deployment의 자동 실행 횟수가 `max_actions_per_deployment`보다 작다.
- 한 주기에서 다른 Action이 이미 실행되지 않았다.
- Action별 필수 입력이 존재한다.

Agent Registry와 증거 검증은 두 모드에서 동일하게 수행한다. 이후 별도 실행 Gate가 모드를 확인한다. `Monitor Only`에서는 승인된 Action을 `would_execute`로 기록하고 종료하며, `Guarded Auto`에서만 AppDeploy 상태 변경 API를 호출한다. 검증 결과는 `approved`, `rejected`, `blocked` 중 하나로 기록한다.

## Action 실행 계약

### observe_status

- `GET /deployments/{deployment_id}`와 metrics를 다시 조회한다.
- 상태를 변경하지 않는다.
- Monitor Only와 Guarded Auto 모두 실행할 수 있다.

### stop_application

- `POST /deployments/{deployment_id}/stop`을 호출한다.
- 이미 terminal 상태이면 상태 조회만 기록하고 성공으로 가장하지 않는다.

### restart_application

1. 현재 Deployment와 Manifest를 조회한다.
2. 현재 Deployment가 실행 중이면 stop API를 호출한다.
3. 동일 Manifest로 `POST /deployments`를 호출한다.
4. 새 Deployment ID를 감시 대상으로 교체한다.

stop은 성공했지만 새 Deployment 생성이 실패하면 `partial_failure` 이벤트를 기록하고 자동 재시도를 중단한다.

### rollback_application

1. `rollback_app_version_id`가 설정됐는지 검증한다.
2. 현재 Deployment Manifest를 복사한다.
3. 복사한 Manifest의 `app_version_id`만 설정된 이전 버전으로 변경한다.
4. 현재 Deployment를 중지한다.
5. 변경된 Manifest로 새 Deployment를 생성한다.
6. 새 Deployment ID를 감시 대상으로 교체한다.

이전 App Version이 설정되지 않으면 `blocked` 처리한다. Qwen이 이전 버전을 임의 생성할 수 없다.

### scale_out_application

1. `standby_target_profile_id`가 설정됐는지 검증한다.
2. 현재 Deployment Manifest를 복사한다.
3. Target Profile ID를 Standby Target으로 변경한다.
4. 추가 Deployment를 생성한다.
5. 새 Deployment ID를 관련 Deployment 목록에 추가한다.

이 동작은 VM-only 추가 Deployment 프로토타입이다. Load Balancer 또는 트래픽 분산 완료로 표시하지 않고 결과에 `traffic_handoff_required: true`를 포함한다. Standby Target이 없으면 `blocked` 처리한다.

## 상태 모델

```text
stopped
  -> monitoring
  -> observing_violation
  -> decision_pending
  -> guard_rejected | action_blocked
  -> executing
  -> cooldown
  -> awaiting_evidence
  -> monitoring

executing
  -> execution_failed | partial_failure
```

Deployment별 런타임 상태는 다음을 포함한다.

- primary deployment ID
- related deployment IDs
- consecutive violation count
- last metric timestamp
- last proposed Action
- last Guard decision
- last execution result
- cooldown expiration time
- automatic action count
- last evaluation result

## 설정 모델

```json
{
  "mode": "monitor_only",
  "poll_interval_seconds": 10,
  "consecutive_violations": 2,
  "cooldown_seconds": 120,
  "max_actions_per_deployment": 3,
  "max_metric_age_seconds": 60,
  "deployment_id": "dep-...",
  "standby_target_profile_id": "target-standby-001",
  "rollback_app_version_id": "appver-previous",
  "slo": {
    "max_latency_ms": 500,
    "min_throughput_rps": 1,
    "max_error_rate": 0.05
  }
}
```

검증 범위:

- poll interval: 2~3600초
- consecutive violations: 1~10회
- cooldown: 0~86400초
- max actions: 1~20회
- max metric age: poll interval 이상, 86400초 이하
- latency/throughput/error rate: 0 이상의 유한 숫자
- error rate: 0~1
- Deployment, Target, App Version ID: 공백이 없는 제한 길이 문자열

## REST API

### GET `/api/v1/autonomy/status`

현재 설정, 루프 상태, AppDeploy 연결 상태, 감시 대상 상태, 최근 평가와 최근 Action을 반환한다.

### PUT `/api/v1/autonomy/config`

전체 설정을 검증하고 교체한다. Credential과 임의 Secret 필드는 허용하지 않는다. `mode=guarded_auto`로 변경해도 루프가 자동 시작되지는 않는다.

### POST `/api/v1/autonomy/start`

설정된 모드로 모니터링 루프를 시작한다. 중복 호출은 새 goroutine을 만들지 않는다.

### POST `/api/v1/autonomy/stop`

정상 중지를 요청한다. 실행 중인 AppDeploy HTTP 요청 context도 취소한다.

### POST `/api/v1/autonomy/emergency-stop`

루프를 즉시 중지하고 모드를 `monitor_only`로 되돌린다. 이미 AppDeploy가 완료한 변경은 되돌리지 않는다.

### POST `/api/v1/autonomy/cycles`

테스트와 시연을 위해 현재 설정으로 한 주기만 실행한다. 루프 실행 중에도 동시 주기가 실행되지 않도록 mutex로 직렬화한다.

### GET `/api/v1/autonomy/events`

최신 이벤트부터 최대 200개를 반환한다. 이벤트에는 timestamp, cycle ID, deployment ID, stage, status, reason, proposal, Guard 결과, 실행 결과가 포함된다.

## 웹 화면

기존 sidebar에 `Autonomous Loop` 메뉴를 `Agents & Guard`와 `Feedback` 사이에 추가한다.

### 상단 제어 영역

- Loop 상태 표시
- `Monitor Only / Guarded Auto` segmented control
- Start/Stop 버튼
- Emergency Stop 버튼
- Run Cycle Now 버튼

### Policy 영역

- Deployment ID
- Max Latency
- Min Throughput
- Max Error Rate
- Poll Interval
- Consecutive Violations
- Cooldown
- Max Actions
- Max Metric Age
- Standby Target
- Rollback App Version

### 관찰 영역

- AppDeploy 연결 상태
- 최신 Metric timestamp
- 현재 Latency/Throughput/Error Rate
- 연속 위반 횟수
- Cooldown 남은 시간
- 최근 Qwen Action과 confidence
- 최근 Go Guard 결과
- 최근 AppDeploy 실행 결과

### Timeline

- `monitor`, `slo`, `qwen`, `guard`, `execute`, `feedback` stage를 시간순으로 표시한다.
- 실패와 차단 이유를 숨기지 않는다.
- 이벤트 원본 JSON을 확인할 수 있다.

모바일에서는 제어 버튼이 두 줄로 배치되고 Policy는 단일 열로 바뀐다. 테이블은 화면 밖으로 UI를 밀지 않고 내부 스크롤을 사용한다.

## 오류 처리

- AppDeploy 연결 실패: 루프를 중단하지 않고 `source_unavailable` 이벤트를 기록한 뒤 다음 주기에 재시도한다.
- Metric 없음/오래됨: `insufficient_evidence`, 상태 변경 Action 없음.
- Qwen 오류/잘못된 JSON: `decision_failed`, AppDeploy 변경 없음.
- Guard 거부: `guard_rejected`, AppDeploy 변경 없음.
- Action 필수 입력 없음: `blocked`, AppDeploy 변경 없음.
- AppDeploy 4xx: `execution_failed`, 자동 재시도 없음.
- AppDeploy retryable 5xx/timeout: 다음 주기까지 기다리며 동일 주기에서는 재실행하지 않는다.
- 부분 실패: `partial_failure`, 해당 Deployment의 자동 실행을 잠그고 Emergency Stop과 동일하게 mode를 `monitor_only`로 변경한다.

## 테스트 전략

### 단위 테스트

- SLO 경계값과 오류율 계산
- 오래된 Metric과 Metric 없음
- 연속 위반 증가/초기화
- Cooldown과 최대 Action 제한
- Action별 필수 입력 검증
- 상태 전이와 이벤트 제한 200개

### AppDeploy Client 계약 테스트

- metrics/summary/deployment list 응답 디코딩
- stop API 요청
- restart의 stop/create 순서
- rollback Manifest의 app_version_id 변경
- scale-out Manifest의 target_profile_id 변경

### Manager 통합 테스트

- Monitor Only에서 변경 API 미호출
- Guarded Auto에서 2회 위반 후 한 번만 실행
- Guard 거부 시 실행 없음
- 중복 start 방지
- stop/emergency stop context 취소
- partial failure 후 monitor_only 복귀

### API 테스트

- config validation
- start/stop/status/events
- cycle 직렬화
- Credential 형태 필드 거부

### 브라우저 검증

- 데스크톱과 모바일에서 가로 overflow 없음
- mode 변경, start/stop, emergency stop
- Metric 반영과 Timeline 갱신
- Monitor Only의 `would_execute`
- Guarded Auto의 실제 AppDeploy 결과 표시

## 완료 조건

1. `Monitor Only`에서 SLO 위반 Metric을 두 번 관찰하면 Qwen과 Go Guard 결과가 Timeline에 나타나고 AppDeploy 상태 변경 요청은 0회다.
2. `Guarded Auto`에서 동일 조건을 만족하면 Go Guard가 승인한 Action 하나만 AppDeploy에 전달된다.
3. 실행 후 Cooldown 동안 추가 상태 변경 Action은 실행되지 않는다.
4. 새 Metric이 정상 범위면 위반 횟수가 초기화되고 `recovered` 이벤트가 기록된다.
5. Standby Target 또는 Rollback Version이 없으면 해당 Action은 `blocked`로 표시된다.
6. Emergency Stop 이후 루프는 중지되고 모드는 `Monitor Only`로 돌아간다.
7. AppDeploy 소스에는 변경이 없다.
