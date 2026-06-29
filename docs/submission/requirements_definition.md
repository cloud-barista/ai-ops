# 요구사항 정의서

영문 제목: Requirements Definition

## 1. 목적

본 문서는 1차년도 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**가 갖추어야 할 필수 요구사항을 정의합니다. 구현 결과 나열이 아니라, 과제 수행과 프로토타입 검증을 위해 필요한 조건을 정리합니다.

## 2. 핵심 요구 범위

| 구분 | 요구 내용 |
| --- | --- |
| LLM 운영 관리 | Ops 분석 시험을 위한 LLM 후보, 선정 기준, 운영 관리 흐름을 정의해야 한다. |
| 에이전트 등록 관리 | AI 에이전트의 역할, 책임, 허용 action 경계를 관리해야 한다. |
| AI 응용 배포·제어 | CPU/GPU VM 기반으로 AI workload 배치와 배포 계획을 판단해야 한다. |
| 검증 방식 | 로컬 Go API/CLI로 반복 가능한 검증 결과를 생성해야 한다. |
| 외부 연동 경계 | 실제 GPU VM 생성과 CB-Tumblebug 연동은 기본 로컬 검증 범위 밖으로 둔다. |

## 3. 기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| FR-01 | Ops LLM 후보와 선정 policy를 정의해야 한다. | candidate, metric, weight가 설정으로 분리되고 ranking 결과를 산출해야 한다. |
| FR-02 | LLM 선정 결과를 설명 가능하게 제공해야 한다. | 선택 candidate, score, ranking, 선정 사유가 포함되어야 한다. |
| FR-03 | AI 에이전트 registry를 제공해야 한다. | agent name, role, responsibility, bounded action을 조회할 수 있어야 한다. |
| FR-04 | 에이전트 action boundary를 검증해야 한다. | 허용되지 않은 action은 ready 상태로 처리하지 않아야 한다. |
| FR-05 | CPU/GPU VM 배치 판단 기준을 제공해야 한다. | accelerator, VRAM, latency SLO, throughput, cost, capacity를 고려해야 한다. |
| FR-06 | AI 응용 배포·제어 계획을 생성해야 한다. | 선택 resource, deployment, node selector, resource limit, monitoring metric이 포함되어야 한다. |
| FR-07 | 통합 서비스 운영 준비도 결과를 제공해야 한다. | LLM 선정, agent 검증, 배치 판단, 배포 계획, guard 검증 결과를 함께 보고해야 한다. |
| FR-08 | CLI와 HTTP API를 모두 제공해야 한다. | 같은 핵심 기능을 command와 API endpoint로 실행할 수 있어야 한다. |
| FR-09 | API 계약을 표준 형식으로 제공해야 한다. | OpenAPI 또는 Swagger 문서가 제공되어야 한다. |

## 4. 비기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| NFR-01 | 핵심 구현은 Go 중심이어야 한다. | 주요 판단 로직과 API/CLI 실행 경로가 Go module에 있어야 한다. |
| NFR-02 | 정책과 입력 데이터는 재현 가능해야 한다. | LLM policy, agent registry, VM profile이 설정 파일로 관리되어야 한다. |
| NFR-03 | 기본 검증은 로컬에서 가능해야 한다. | cloud credential 없이 mock 또는 dry-run 방식으로 검증되어야 한다. |
| NFR-04 | prototype과 production을 구분해야 한다. | 실제 운영 배포, live cluster 변경, GPU VM 생성을 과장해 표현하지 않아야 한다. |
| NFR-05 | benchmark 경계를 명확히 해야 한다. | 수동 policy baseline과 최종 정량 benchmark를 구분해야 한다. |
| NFR-06 | 검증 결과를 추적 가능하게 남겨야 한다. | 주요 실행 결과는 JSON 또는 문서화 가능한 출력으로 보존되어야 한다. |

## 5. 산출물 요구사항

| 산출물 | 요구사항 |
| --- | --- |
| 요구사항 정의서 | 범위, 기능 요구사항, 비기능 요구사항, 검증 기준, 제외 범위를 정의해야 한다. |
| LLM 운영 관리 구조 설계서 | LLM 후보 선정 기준과 운영 관리 구조를 설명해야 한다. |
| 에이전트 등록 관리 프로토타입 | agent registry와 bounded action 검증 방식을 설명해야 한다. |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | CPU/GPU VM 기반 배치 판단과 배포·제어 전략을 설명해야 한다. |
| 기능/API 가이드 | API 실행 방법과 request/response 구조를 설명해야 한다. |
| 테스트 가이드 | Go test, team-validation, 실패 로그 보존 방법을 설명해야 한다. |

## 6. 검증 기준

기본 검증은 다음 Go 명령으로 수행할 수 있어야 합니다.

```bash
cd go/aiops-guard
go test ./...

cd ../service-control-api
go test ./...
go run ./cmd/aiops-service-control team-validation
```

기대 신호:

```text
valid = true
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
guard_backend = go
guard_validation.valid = true
```

## 7. 제외 및 주의 범위

- 최종 표준 LLM benchmark 결과를 주장하지 않는다.
- 실제 GPU VM provisioning 완료를 주장하지 않는다.
- 기본 `mock` mode는 live Kubernetes cluster를 변경하지 않는다.
- CB-Tumblebug과 AI-Infra는 대체 대상이 아니라 향후 연동 대상이다.
