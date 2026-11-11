# 요구사항 정의서

English title: Requirements Definition

## 1. 목적

본 문서는 1차년도 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**가 충족해야 할 기능, 검증 기준과 책임 경계를 정의합니다. 구현 결과를 나열하기보다 실제 연구 수행에 필요한 입력, 판단, 출력과 수용 조건을 중심으로 작성합니다.

## 2. 적용 범위

| 영역 | 요구 범위 |
| --- | --- |
| AI LLM 운영 관리 | Ops 시나리오 기반 LLM 후보 평가, 선정 근거와 실행 상태 관리 |
| AI 응용 자동화 에이전트 | 실제 LLM 기반 bounded Action 제안과 Go 정책 검증 |
| 에이전트 등록 관리 | 내부 자동화 에이전트와 외부 실행 주체의 역할, capability, bounded Action과 endpoint 관리 |
| VM 적합성 검증 | 인프라 계층이 제공한 실제 CPU/GPU VM 정보와 workload 요구사항 비교 |
| 배포·제어 계획 | 검증 결과를 범용 외부 실행 에이전트로 전달할 비실행 handoff 계획 생성 |
| 구현·검증 | Go API/CLI, 테스트, 환경 증적과 실행 결과 저장 |

## 3. 핵심 입력

| 입력 | 위치 또는 형식 | 요구사항 |
| --- | --- | --- |
| LLM 정책·후보 | `config/ops_llm_benchmark.json` | candidate, metric, policy weight와 평가 상태를 구분한다. |
| LLM 실행 candidate | OpenAI-compatible candidate config | provider, endpoint, actual model, timeout과 secret 환경 변수명을 분리한다. |
| 에이전트 registry | `config/agent_registry.json`, 등록 API | capability와 허용 Action을 명시한다. |
| Workload 요구사항 | `config/vm_workload_requirements.json` | 필요한 accelerator와 검증 가능한 최소 조건만 선언한다. |
| 실제 VM snapshot | API body 또는 `--vm-snapshot` JSON | 출처, 수집 상태, instance type, CPU·메모리, GPU·VRAM, driver·CUDA를 사실값으로 기록한다. |

VM 후보 목록과 예상 성능값을 임의로 생성하지 않습니다. VM 생성·조회는 CB-Tumblebug을 포함한 외부 인프라 계층의 책임이며, 본 prototype은 제공받은 VM만 검증합니다.

## 4. 기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| FR-01 | Ops LLM 후보를 정책에 따라 평가한다. | 선정 결과에 model, provider, score, rationale, benchmark status가 포함된다. |
| FR-02 | 실제 평가와 baseline을 구분한다. | 실제 endpoint가 수행된 경우에만 `benchmark_status=executed`를 기록한다. |
| FR-03 | 실제 LLM이 자동화 Action을 제안한다. | endpoint 호출 성공 시 실제 model, latency, reason, confidence와 `decision_execution_status=executed`를 기록한다. |
| FR-04 | LLM 실패를 성공으로 대체하지 않는다. | provider 실패, 잘못된 JSON, 허용 범위 밖 Action은 `llm_failed` 또는 `rejected`로 반환한다. |
| FR-05 | 에이전트를 등록·조회한다. | 내부 자동화 에이전트와 외부 실행 주체의 capability, endpoint와 bounded Action을 조회할 수 있다. |
| FR-06 | 에이전트 Action을 검증한다. | 등록된 허용 Action만 승인하며 거부 사유를 반환한다. |
| FR-07 | 실제 VM의 workload 적합성을 검증한다. | 입력 snapshot의 출처와 수집 상태를 확인하고 선언된 accelerator·자원 조건을 비교한다. |
| FR-08 | 미측정 성능을 구분한다. | latency, throughput, cost가 없으면 `not_measured`와 `provisionally_compatible`을 반환한다. |
| FR-09 | 범용 제어 handoff를 생성한다. | capability, Action, 선택된 실행 주체, correlation ID와 `not_executed` 상태를 제공한다. |
| FR-10 | 실행 feedback을 기록한다. | 승인된 correlation ID에 대해 정규화된 상태와 선택적 성능값을 기록한다. |
| FR-11 | 특정 실행 프레임워크에 종속되지 않는다. | capability와 Action이 맞는 등록 외부 실행 주체를 동적으로 선택한다. |
| FR-12 | CLI와 HTTP API를 제공한다. | LLM 선정·Action 제안, registry, VM 적합성, handoff를 두 방식으로 호출할 수 있다. |
| FR-13 | VM 환경 증적을 수집한다. | `validate-system --target vm`이 CPU·메모리·GPU·VRAM·driver·CUDA와 AWS metadata를 저장한다. |

## 5. 비기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| NFR-01 | Go 중심 구현 | 핵심 판단과 API/CLI가 Go module에 존재한다. |
| NFR-02 | 재현 가능성 | 설정과 입력 snapshot이 JSON으로 관리된다. |
| NFR-03 | 안전한 제어 | 제어 Action은 registry와 Go Guard 경계를 통과해야 한다. |
| NFR-04 | 사실 기반 보고 | 예상값, 측정값, 미측정값과 실행 여부를 혼합하지 않는다. |
| NFR-05 | 민감정보 보호 | credential, token, API key, kubeconfig를 저장소에 커밋하지 않는다. |
| NFR-06 | 추적 가능성 | branch, commit, Go test, VM 환경과 단계별 결과를 증적으로 남긴다. |

## 6. 검증 기준

| 검증 | 기대 결과 |
| --- | --- |
| `make test` | 두 Go module 테스트 통과 |
| `make vet` | 두 Go module 정적 검사 통과 |
| `team-validation` | LLM 선정, registry, 실제 snapshot 적합성, handoff 계획 결과 생성 |
| `validate-system --target local` | 로컬 Go·API 흐름 검증 |
| `validate-system --target vm` | 라이브 VM 자원 snapshot과 환경 증적 생성 |
| 실제 LLM benchmark | endpoint 실행 시에만 `benchmark_status=executed` |
| 실제 LLM Action 결정 | `--run-llm-decision` 사용 시 `decision_execution_status=executed`와 Go Guard 결과 저장 |

대표 명령:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

## 7. 제외 범위와 한계

| 항목 | 경계 |
| --- | --- |
| VM 프로비저닝 | 본 prototype이 수행하지 않으며 외부 인프라 계층 결과를 입력받는다. |
| 실제 배포 실행 | 등록 에이전트 handoff 계획까지만 생성하며 결과는 `not_executed`이다. |
| 최종 성능 최적화 | workload latency·throughput·cost 측정 전에는 잠정 적합성만 판단한다. |
| 특정 팀 프레임워크 고정 | 허용하지 않는다. 모든 실행 주체는 동일한 registry 계약으로 연결한다. |
| 컨테이너·Kubernetes 배포 | 1차년도 VM-only 범위에서 제외한다. |
