# 요구사항 정의서

영문 제목: Requirements Definition

## 1. 문서 목적

본 문서는 1차년도 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**의 요구사항을 정의합니다. 목적은 구현 결과를 단순 나열하는 것이 아니라, 연구 과제 수행에 필요한 기능, 품질, 검증 기준, 제외 범위를 명확히 정리하는 것입니다.

## 2. 과제 적용 범위

| 구분 | 요구 범위 | 설명 |
| --- | --- | --- |
| AI LLM 운영 관리 | Ops LLM 선정과 운영 관리 구조 | Ops 분석 시험 결과를 기반으로 사용할 LLM 후보를 선정하고 운영 관리 흐름에 연결해야 한다. |
| 에이전트 등록 관리 | AI agent registry와 action boundary | 서비스 제어에 참여하는 agent의 역할, 책임, 허용 action을 관리해야 한다. |
| AI 응용 배포·제어 | CPU/GPU VM 기반 추론 배치 판단 | AI workload 요구사항을 기반으로 CPU/GPU VM 배치를 추천하고 배포 계획을 생성해야 한다. |
| 실행 방식 | Go API/CLI | 제출 및 시연 경로의 핵심 로직은 Go 기반으로 실행 가능해야 한다. |
| 검증 방식 | 로컬 반복 검증 | cloud credential 없이도 mock 또는 dry-run으로 기능 흐름을 검증할 수 있어야 한다. |

## 3. 핵심 입력 데이터

| 입력 | 위치 | 요구사항 |
| --- | --- | --- |
| LLM 정책 후보 | `config/ops_llm_benchmark.json` | LLM candidate, metric, policy weight를 재현 가능한 형태로 정의해야 한다. |
| Agent registry | `config/agent_registry.json` | agent role, responsibility, bounded action, reward signal을 정의해야 한다. |
| 추론 배치 정책 | `config/inference_optimization.json` | workload와 CPU/GPU VM resource profile을 정의해야 한다. |

## 4. 기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| FR-01 | Ops LLM 후보와 선정 policy를 정의해야 한다. | policy 요청 시 candidate ranking과 selected model을 산출해야 한다. |
| FR-02 | LLM 선정 결과는 설명 가능해야 한다. | selected model, score, ranking, rationale을 제공해야 한다. |
| FR-03 | AI agent registry를 제공해야 한다. | agent 목록과 단일 agent 상세 정보를 조회할 수 있어야 한다. |
| FR-04 | Agent action boundary를 검증해야 한다. | 허용 action만 valid 처리하고, 거부 사유를 제공해야 한다. |
| FR-05 | CPU/GPU VM 배치 판단 기준을 제공해야 한다. | accelerator, VRAM, latency SLO, throughput, cost, capacity를 고려해야 한다. |
| FR-06 | AI 응용 배포·제어 계획을 생성해야 한다. | deployment name, namespace, node selector, resource limit, monitoring metric을 포함해야 한다. |
| FR-07 | 서비스 운영 준비도를 통합 보고해야 한다. | LLM 선정, agent 검증, 배치 판단, 배포 계획, guard 검증 결과를 함께 제공해야 한다. |
| FR-08 | CLI와 HTTP API를 모두 제공해야 한다. | 동일 기능을 command와 API endpoint로 실행할 수 있어야 한다. |
| FR-09 | API 계약을 표준 형식으로 제공해야 한다. | OpenAPI 또는 Swagger 문서로 request/response 구조를 확인할 수 있어야 한다. |

## 5. 비기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| NFR-01 | Go 중심 구현 | 주요 판단 로직과 실행 경로가 Go module에 있어야 한다. |
| NFR-02 | 재현 가능성 | 정책과 입력 데이터가 JSON 설정으로 관리되어야 한다. |
| NFR-03 | 로컬 검증 가능성 | 기본 검증은 cloud credential 없이 실행 가능해야 한다. |
| NFR-04 | 안전한 action 처리 | agent action과 service-control action은 허용 범위 안에서만 ready 처리되어야 한다. |
| NFR-05 | 경계 명확화 | prototype baseline과 최종 benchmark 결과를 구분해야 한다. |
| NFR-06 | 검토 가능성 | 실행 결과와 산출물은 reviewer가 추적 가능한 형태로 남아야 한다. |

## 6. 제출 산출물 요구사항

| 산출물 | 요구사항 |
| --- | --- |
| 요구사항 정의서 | 과제 범위, 기능 요구사항, 비기능 요구사항, 검증 기준, 제외 범위를 정의한다. |
| LLM 운영 관리 구조 설계서 | LLM 선정 기준, 운영 흐름, score 해석, 검증 방법을 설명한다. |
| 에이전트 등록 관리 프로토타입 | agent registry 구조, agent 역할, bounded action 검증을 설명한다. |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | CPU/GPU VM 배치 기준, scoring, AI 응용 배포·제어 계획, 검증 방식을 설명한다. |
| 기능/API 가이드 | endpoint, request/response, 실행 예시를 설명한다. |
| 테스트 가이드 | Go test, team-validation, 실패 로그 보존 기준을 설명한다. |

## 7. 검증 요구사항

| 검증 항목 | 요구 결과 |
| --- | --- |
| Go guard test | bounded action validation이 정상 동작해야 한다. |
| Service-control API test | LLM 선정, agent registry, placement, AI 응용 배포·제어 계획 로직이 검증되어야 한다. |
| Team validation | 주요 기능이 순차 실행되고 전체 `valid=true` 결과를 생성해야 한다. |
| DOCX 확인 | 제출본 DOCX가 Markdown 원본과 동일한 제목과 핵심 내용을 포함해야 한다. |

기본 검증 명령:

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

## 8. 제외 및 제약

| 항목 | 설명 |
| --- | --- |
| 최종 LLM benchmark | 현재 LLM score는 prototype baseline이며 최종 정량 benchmark가 아니다. |
| 실제 GPU VM 생성 | 기본 로컬 검증은 실제 AWS GPU VM을 생성하지 않는다. |
| Live cluster 변경 | 기본 `mock` mode는 live Kubernetes cluster를 변경하지 않는다. |
| CB-Tumblebug 대체 | CB-Tumblebug은 대체 대상이 아니라 향후 AI-Infra 연동 대상이다. |
