# 요구사항 정의서

영문 제목: Requirements Definition

## 1. 문서 개요

이 문서는 1차년도 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**에서 요구되는 기능, 품질, 산출물, 검증 기준을 정의합니다. 요구사항은 특정 구현 결과를 나열하기 위한 것이 아니라, 연구 과제 수행과 프로토타입 검증을 위해 시스템이 갖추어야 할 필요 조건을 정리한 것입니다.

본 문서는 요구사항 정의서 제출 산출물의 Markdown 원본입니다. DOCX 제출본은 이 원본을 기준으로 생성합니다.

## 2. 요구 범위

1차년도 요구 범위는 AI LLM 운영 관리와 AI 응용 배포·제어 판단을 로컬에서 재현 가능한 형태로 검증할 수 있는 기능 프로토타입입니다. 시스템은 Go 기반 API/CLI로 실행 가능해야 하며, 정책과 입력 데이터는 검토 가능한 설정 파일로 분리되어야 합니다.

필수 포함 범위는 다음과 같습니다.

- Ops 분석 시험을 위한 LLM 후보 선정 기준 정의
- AI LLM 운영 관리 구조와 실행 흐름 정의
- AI 에이전트 등록 정보와 허용 action 경계 관리
- CPU/GPU VM 기반 AI 응용 배치 판단 기준 정의
- AI 응용 배포·제어 계획 생성
- 로컬 환경에서 반복 가능한 검증 결과 생성

기본 요구 범위에서 제외되는 항목은 다음과 같습니다.

- 운영 환경용 완성형 AIOps platform 구축
- 최종 표준 LLM benchmark 결과 산출
- 실제 GPU VM provisioning 직접 수행
- 기본 실행 경로에서 live Kubernetes cluster 변경
- CB-Tumblebug 또는 AI-Infra provisioning component 대체

## 3. 기능 요구사항

| ID | 요구사항 | 우선순위 | 수용 기준 |
| --- | --- | --- | --- |
| FR-01 | 시스템은 Ops 분석 시험을 위해 LLM 후보, 평가 기준, 선정 policy를 정의할 수 있어야 한다. | 필수 | LLM 후보와 policy weight가 검토 가능한 설정으로 분리되고, 요청 policy에 따라 후보 ranking과 선정 결과를 산출할 수 있어야 한다. |
| FR-02 | 시스템은 선정된 LLM 후보가 AI LLM 운영 관리 흐름에서 어떤 역할을 수행하는지 설명 가능한 구조를 가져야 한다. | 필수 | 선정 결과에는 candidate label, 점수, ranking, 선정 사유가 포함되어야 하며, 최종 benchmark 결과와 prototype 기준값을 구분해야 한다. |
| FR-03 | 시스템은 AI 에이전트를 등록하고, 각 에이전트의 역할·책임·허용 action을 관리할 수 있어야 한다. | 필수 | 에이전트 목록 조회, 단일 에이전트 확인, 허용 action 검증이 가능해야 한다. |
| FR-04 | 시스템은 에이전트가 제안하거나 승인하는 action이 사전에 정의된 boundary 안에 있는지 검증해야 한다. | 필수 | 허용되지 않은 action은 ready 상태로 처리되지 않아야 하며, 검증 결과에는 승인 여부와 사유가 포함되어야 한다. |
| FR-05 | 시스템은 AI workload 요구사항을 기준으로 CPU/GPU VM resource 후보를 평가할 수 있어야 한다. | 필수 | accelerator 필요 여부, model type, VRAM, latency SLO, throughput, cost, capacity를 판단 기준으로 사용할 수 있어야 한다. |
| FR-06 | 시스템은 AI 응용 배포·제어를 위한 resource 선택 결과와 배포 계획을 생성할 수 있어야 한다. | 필수 | 선택 resource, 배포 action, Kubernetes namespace/deployment, node selector, resource request/limit, monitoring metric이 포함되어야 한다. |
| FR-07 | 시스템은 LLM 선정, 에이전트 검증, CPU/GPU 배치, 배포 계획, guard 검증을 하나의 서비스 운영 준비도 결과로 통합할 수 있어야 한다. | 필수 | 통합 결과에는 전체 valid 여부와 단계별 판단 결과가 포함되어야 한다. |
| FR-08 | 시스템은 CLI와 HTTP API 양쪽에서 주요 기능을 실행할 수 있어야 한다. | 필수 | 동일한 핵심 기능을 명령행과 API endpoint로 검증할 수 있어야 한다. |
| FR-09 | 시스템은 API 계약을 외부 검토자가 확인할 수 있는 표준 형식으로 제공해야 한다. | 필수 | OpenAPI 또는 Swagger 형식의 API 계약 문서가 제공되어야 한다. |

## 4. 비기능 요구사항

| ID | 요구사항 | 수용 기준 |
| --- | --- | --- |
| NFR-01 | 제출/시연 경로의 핵심 구현은 Go 언어 중심이어야 한다. | 핵심 판단 로직과 API/CLI 실행 경로가 Go module 안에 위치해야 한다. |
| NFR-02 | 정책, 후보, resource profile은 재현 가능한 설정으로 관리해야 한다. | LLM policy, agent registry, CPU/GPU VM profile이 소스 코드와 분리된 설정 파일로 관리되어야 한다. |
| NFR-03 | 기본 검증은 외부 cloud credential 없이 실행 가능해야 한다. | 로컬 환경에서 mock 또는 dry-run 방식으로 주요 기능 흐름을 검증할 수 있어야 한다. |
| NFR-04 | prototype 검증과 production operation을 명확히 구분해야 한다. | 문서와 출력에서 운영 환경 배포 완료, 실제 GPU VM 생성, live cluster 변경을 과장해 표현하지 않아야 한다. |
| NFR-05 | LLM 선정 기준값과 최종 benchmark 결과를 명확히 구분해야 한다. | 수동 정의 policy baseline은 prototype 입력값으로 설명하고, 최종 정량 평가는 별도 통제 실험이 필요함을 명시해야 한다. |
| NFR-06 | 실행 결과는 검토자가 추적할 수 있는 형태로 남아야 한다. | 주요 검증 명령은 JSON 또는 문서화 가능한 출력 결과를 생성해야 한다. |

## 5. 제출 산출물 요구사항

| 필수 산출물 | 형식 | 요구사항 |
| --- | --- | --- |
| 요구사항 정의서 | Markdown, DOCX | 과제 범위, 기능 요구사항, 비기능 요구사항, 검증 기준, 제외 범위를 정의해야 한다. |
| 기능/API 가이드 | Markdown | API 실행 방법, endpoint, request/response 구조를 설명해야 한다. |
| Swagger/OpenAPI 계약 | YAML | HTTP API 계약을 표준 형식으로 제공해야 한다. |
| 설치 및 실행 가이드 | Markdown | Go 환경 설정, CLI/API 실행, mock mode, 기대 출력을 설명해야 한다. |
| 테스트 가이드 | Markdown | Go test, team-validation, 실패 로그 보존 방법을 설명해야 한다. |
| LLM 운영 관리 구조 설계서 | Markdown, DOCX | LLM 후보 선정과 운영 관리 구조를 설명해야 한다. |
| 에이전트 등록 관리 프로토타입 | Markdown, DOCX | 에이전트 등록 정보, role, bounded action, 검증 방식을 설명해야 한다. |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | Markdown, DOCX | CPU/GPU VM 기반 배치 판단과 배포·제어 전략을 설명해야 한다. |

## 6. 개발 및 검증 요구사항

개발 및 검증 과정은 다음 조건을 만족해야 합니다.

- Go 기반 API/CLI로 핵심 기능을 실행할 수 있어야 한다.
- 2종 이상 LLM 또는 코딩 에이전트 역할을 활용한 교차 검토 기록을 남겨야 한다.
- 프롬프트 공유 문서는 민감정보를 제거한 대표 template 중심으로 작성해야 한다.
- 오류 발생 시 command, working directory, stdout/stderr, 환경 정보를 보존해야 한다.
- 생성 문서와 DOCX 제출본은 실제 파일 존재 여부를 확인한 뒤 사용 가능하다고 표시해야 한다.
- 사람 검토자는 prototype boundary와 benchmark boundary가 문서에 명확히 들어갔는지 확인해야 한다.

실제 사용 여부가 확인되지 않은 특정 LLM vendor명이나 coding-agent product명을 임의로 만들지 않습니다. 필요한 경우 `Agent A`, `Agent B`, `primary coding agent`, `secondary review agent`와 같은 중립적 역할명을 사용합니다.

## 7. 검증 기준

기본 검증은 로컬 Go 실행으로 수행할 수 있어야 합니다.

```bash
cd go/aiops-guard
go test ./...
```

```bash
cd go/service-control-api
go test ./...
```

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

기대되는 prototype-level signal은 다음과 같습니다.

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

위 검증은 기능 흐름이 요구사항을 만족하는지 확인하기 위한 것입니다. 운영 성능, 표준 LLM benchmark 품질, 실제 GPU VM provisioning을 증명하지 않습니다.

## 8. 경계 및 제약 조건

현재 LLM policy value는 prototype 요구사항 검증을 위한 기준 입력값입니다. 최종 표준 benchmark 결과가 아니며, 최종 정량 보고를 위해서는 고정 prompt, 고정 dataset, 반복 가능한 metric, 문서화된 scoring rule을 갖춘 통제된 per-model Ops 평가가 필요합니다.

CPU/GPU VM 배치 판단은 추천 및 배포 계획 생성 요구사항을 검증하기 위한 기능입니다. 운영 cloud scheduler, Kubernetes scheduler, GPU device plugin, CB-Tumblebug provisioning을 대체하지 않습니다.

기본 `mock` mode는 live Kubernetes cluster를 변경하지 않아야 합니다. 실제 cloud credential과 GPU VM 환경이 필요한 실험은 AI-Infra 또는 CB-Tumblebug 연동 단계에서 별도로 수행해야 합니다.
