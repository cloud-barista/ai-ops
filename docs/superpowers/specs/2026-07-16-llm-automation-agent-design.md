# LLM 기반 AI 응용 자동화 에이전트 설계

## 1. 목적

현재 프로토타입은 Ops LLM 후보 선정과 benchmark를 제공하지만, AI 응용 배포·제어 Action은 Go 규칙으로 생성한다. 본 설계는 실제 LLM endpoint가 Action 후보를 제안하고, Go 코드가 정책·권한·실제 VM 조건을 검증하는 구조로 전환한다.

핵심 원칙은 다음과 같다.

- LLM은 판단과 설명을 생성한다.
- 실제 VM 사실값 검증과 정책 집행은 Go가 담당한다.
- 특정 실행 프레임워크 이름에 종속되지 않는다.
- 허용되지 않은 Action은 외부 실행 에이전트로 전달하지 않는다.
- LLM 호출 실패를 규칙 기반 성공 결과로 위장하지 않는다.

## 2. 범위

### 포함

- OpenAI-compatible endpoint를 사용하는 LLM Planner
- 실제 workload, VM snapshot, 운영 상태를 포함한 prompt 구성
- 구조화된 LLM Action 제안
- JSON schema, allowed Action, Agent Registry, Go Guard 검증
- capability 기반 외부 실행 에이전트 handoff 계획
- LLM 모델·provider·latency·응답·검증 결과 증적
- 로컬과 AWS VM에서 동일한 검증 명령 사용

### 제외

- 특정 실행 프레임워크에 대한 하드코딩
- CB-Tumblebug 자체 재구현
- Kubernetes 및 컨테이너 배포
- LLM이 credential을 보거나 직접 클라우드 API를 호출하는 기능
- 검증을 우회한 자동 실행

## 3. 구성요소

### AIApplicationAutomationAgent

본 프로젝트의 중심 LLM 기반 에이전트다. workload 요구사항, 실제 VM snapshot, VM 적합성 결과, 운영 상태와 허용 Action 목록을 입력받아 하나의 Action 후보를 생성한다.

### LLM Provider Adapter

OpenAI-compatible API 계약을 사용한다. Ollama, vLLM, 연구용 GPU 서버 또는 외부 LLM endpoint는 동일한 adapter로 교체할 수 있다. 실행 시 candidate ID를 명시하며 실제 호출 모델과 provider를 결과에 기록한다.

### VM Suitability Validator

CPU, memory, accelerator, VRAM, driver, CUDA와 측정된 latency·throughput을 Go로 검증한다. LLM은 이 값을 수정하거나 적합성 판정을 우회할 수 없다.

### Agent Registry

외부 실행 에이전트를 `capability + bounded Action`으로 검색한다. 특정 이름은 필수 조건이 아니다.

### Go Guard

LLM Action이 workload 허용 목록과 등록 에이전트 권한 범위에 포함되는지 검증한다. 승인된 Action만 handoff 계획에 포함한다.

## 4. 처리 흐름

```text
AI 응용 요청
  -> 실제 VM 적합성 검증
  -> LLM Planner prompt 구성
  -> 실제 LLM endpoint 호출
  -> 구조화 Action 제안 파싱
  -> allowed Action 검증
  -> Agent Registry capability 조회
  -> Go Guard 승인 또는 거부
  -> non-executing handoff 계획 생성
  -> 외부 실행 결과 feedback 수신
```

VM 자원 조건이 실패하면 LLM을 호출하지 않는다. VM 자원은 적합하지만 성능이 미측정이면 LLM 입력에 `provisionally_compatible`와 측정 필요 조건을 명시한다.

## 5. LLM 입력 계약

LLM prompt에는 다음 정보만 포함한다.

- workload ID와 service name
- workload가 선언한 최소 자원 및 SLO
- credential과 주소를 제거한 실제 VM snapshot
- VM compatibility status와 검증 check
- 현재 운영 상태와 관측 지표
- 선택 가능한 bounded Action 목록
- Action별 필요한 capability
- 반드시 지켜야 하는 JSON 출력 형식

실제 PEM key, API key, password, credential reference와 불필요한 public IP는 prompt에 포함하지 않는다.

## 6. LLM 출력 계약

```json
{
  "action": "observe_status",
  "reason": "The VM resource checks passed, but workload performance is not measured.",
  "confidence": 0.82,
  "required_capability": "ai_application_deployment_control",
  "target_vm_id": "recorded-vm-id",
  "parameters": {
    "next_check": "measure_workload_performance"
  }
}
```

필수 필드는 `action`, `reason`, `confidence`, `required_capability`, `target_vm_id`다. `confidence`는 0 이상 1 이하여야 한다. `action`은 prompt에 전달된 허용 목록 중 하나여야 한다.

## 7. 상태와 실패 처리

| 상황 | 상태 | 처리 |
| --- | --- | --- |
| 실제 endpoint 응답 및 전체 검증 통과 | `approved` | handoff 계획 생성 |
| LLM JSON 파싱 실패 | `rejected` | 외부 전달 금지 |
| 허용되지 않은 Action | `rejected` | Go Guard 사유 기록 |
| 실행 가능한 등록 에이전트 없음 | `pending_executor` | 등록 선행 조건 반환 |
| LLM endpoint 실패 또는 timeout | `llm_failed` | 임의 fallback 없이 종료 |
| VM 자원 조건 실패 | `vm_incompatible` | LLM 호출 생략 |
| 성능 미측정 | `measurement_required` | 관측 또는 benchmark Action만 허용 |

`benchmark_status=executed`는 모델 비교 benchmark가 실제 수행됐다는 의미다. 개별 Action 제안 결과에는 별도로 `decision_execution_status=executed`를 기록하여 모델 평가와 운영 판단 실행을 구분한다.

## 8. 외부 실행 경계

LLM Planner 결과가 승인되면 Agent Registry에서 필요한 capability와 Action을 지원하는 실행 에이전트를 찾는다. 실행 대상은 등록 정보에 따라 동적으로 결정한다.

handoff 계획은 다음을 포함한다.

- selected executor
- target URL
- approved Action
- target VM ID
- parameters
- correlation ID
- execution status `not_executed`
- feedback required `true`

본 service-control prototype은 기본적으로 계획과 검증을 담당한다. 외부 실행 결과는 correlation ID와 함께 feedback API로 반환하며, 실제 배포 완료는 feedback이 수신된 경우에만 기록한다.

## 9. API 변경

추가할 핵심 API는 다음과 같다.

- `POST /api/v1/automation/action-proposals`: LLM Action 제안 및 Go Guard 검증
- `POST /api/v1/automation/plans`: 승인된 제안의 외부 handoff 계획 생성
- `POST /api/v1/automation/feedback`: 외부 실행 상태와 측정값 수신

기존 VM suitability, Agent Registry, Ops LLM benchmark API는 유지한다.

## 10. 검증

### 단위 검증

- 정상 LLM JSON 파싱
- malformed JSON 거부
- 허용되지 않은 Action 거부
- confidence 범위 검증
- VM ID 변경 시도 거부
- capability가 없는 에이전트 제외
- endpoint timeout 시 fallback 성공 금지

### 통합 검증

- 가짜 OpenAI-compatible test server를 사용한 실제 HTTP 호출
- 실제 VM snapshot과 LLM 제안 연결
- Go Guard 승인·거부 분기
- 복수 실행 에이전트 중 capability 기반 선택
- feedback correlation ID 연결

### 실제 환경 검증

- AWS GPU VM에서 실제 LLM endpoint 호출
- `decision_execution_status=executed` 확인
- 모델명, provider, latency와 Action 증적 저장
- 로컬과 VM에서 동일한 입력 계약 및 검증 결과 비교

## 11. 완료 기준

- 실제 LLM 응답이 Action 제안의 출처로 기록된다.
- Go 규칙으로 만든 Action을 LLM 결과처럼 표시하지 않는다.
- VM 사실값과 허용 Action은 Go가 최종 검증한다.
- 특정 외부 실행 프레임워크 이름이 코드 필수값이 아니다.
- LLM 실패, Guard 거부, 실행 미수행 상태를 명확히 구분한다.
- `make test`, `make vet`, `make lint`가 통과한다.
