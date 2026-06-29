# 요구사항 정의서

영문 제목: Requirements Definition

## 1. 문서 개요

이 문서는 1차년도 Go 기반 서비스 제어 기능 프로토타입의 기능 요구사항, 비기능 요구사항, 제출 요구사항, 개발 요구사항, 검증 요구사항을 정의합니다. 프로토타입은 AI LLM 운영 관리, AI 에이전트 등록 관리, CPU/GPU VM 배치 추천, AI 응용 배포·제어 준비도 검증을 지원합니다.

본 문서는 요구사항 정의서 제출 산출물의 Markdown 원본입니다. DOCX 제출본은 이 원본을 기준으로 생성합니다.

## 2. 프로젝트 범위

프로젝트 범위는 1차년도 기능 프로토타입으로 제한합니다. 저장소는 담당 연구 범위를 입증하기 위한 Go API/CLI 구성요소, JSON 설정 파일, 설계 산출물, 검증 문서를 제공합니다.

포함 범위는 다음과 같습니다.

- Ops LLM 선정 정책 프로토타입
- AI LLM 운영 관리 구조 검증
- 에이전트 registry 조회 및 bounded action 검증
- AI 응용 워크로드를 위한 CPU/GPU VM 배치 추천
- Kubernetes 배포 계획 생성
- mock 배포 dry-run 및 서비스 운영 준비도 보고

기본 검증 경로의 제외 범위는 다음과 같습니다.

- 운영 환경용 완성형 AIOps 운영
- 최종 표준 LLM 벤치마크 보고
- 실제 GPU VM 프로비저닝
- 기본값 기준 live Kubernetes 변경
- CB-Tumblebug 또는 AI-Infra 프로비저닝 구성요소 대체

## 3. 기능 요구사항

| ID | 기능 요구사항 | 구현 상태 |
| --- | --- | --- |
| FR-01 | JSON으로 정의한 candidate role과 policy weight를 기반으로 Ops LLM 선정 정책 프로토타입을 제공해야 한다. | 구현 |
| FR-02 | 정책 기반 LLM 후보 ranking을 Go API/CLI 명령으로 제공해야 한다. | 구현 |
| FR-03 | 에이전트 registry 조회, 단일 에이전트 확인, bounded action 검증을 제공해야 한다. | 구현 |
| FR-04 | accelerator 요구, SLO, 처리량, 비용, capacity를 기준으로 CPU/GPU VM 배치를 추천해야 한다. | 구현 |
| FR-05 | 선택된 AI 응용 자원에 대한 Kubernetes 배포·제어 계획을 생성해야 한다. | 구현 |
| FR-06 | LLM 선정, 배치 추천, 배포 계획 생성, 에이전트 검토, mock dry-run, guard 준비도 검증을 결합한 통합 서비스 운영 준비도 보고서를 생성해야 한다. | 구현 |
| FR-07 | 주요 기능을 Echo 기반 Go HTTP API로 제공해야 한다. | 구현 |
| FR-08 | 서비스 제어 API의 OpenAPI 계약을 제공해야 한다. | 구현 |

## 4. 비기능 요구사항

| ID | 비기능 요구사항 | 근거 |
| --- | --- | --- |
| NFR-01 | 제출/시연 경로의 구현은 Go 중심으로 유지해야 한다. | 개발 언어 요구사항에 맞추고 혼합 언어 프로토타입의 모호성을 줄인다. |
| NFR-02 | LLM, 에이전트, 배치 정책은 JSON 설정으로 재현 가능해야 한다. | 검토 가능하고 반복 가능한 기능 검증을 지원한다. |
| NFR-03 | 기본 실행 경로는 mock 검증을 사용해야 한다. | live Kubernetes 또는 GPU VM 인프라 없이 로컬 검증을 가능하게 한다. |
| NFR-04 | 프로토타입 검증과 운영 환경 배포를 명확히 구분해야 한다. | 기능 프로토타입이 운영 시스템으로 오해되는 것을 방지한다. |
| NFR-05 | 프로토타입 정책 기준값과 최종 벤치마크 결과를 명확히 구분해야 한다. | 수동 정의 LLM 정책 값이 표준 모델 평가 결과로 해석되는 것을 방지한다. |
| NFR-06 | 불필요한 외부 의존성과 핵심 범위 밖 실험 runner를 제거해야 한다. | 담당 연구 산출물 중심으로 저장소를 유지한다. |

## 5. 제출 산출물 요구사항

| 필수 산출물 | 형식 | 저장소 경로 | 요구사항 |
| --- | --- | --- | --- |
| 요구사항 정의서 원본 | Markdown | `docs/submission/requirements_definition.md` | 범위, 요구사항, 검증 방법, 경계 조건을 정의해야 한다. |
| 요구사항 정의서 제출본 | DOCX | `docs/submission/requirements_definition.docx` | Markdown 원본을 기준으로 생성되어야 한다. |
| 기능/API 가이드 | Markdown | `docs/submission/functional_api_guide.md` | API 서버 실행, endpoint, request 예시, response 필드를 설명해야 한다. |
| Swagger/OpenAPI | YAML | `docs/submission/openapi_service_control.yaml` | HTTP API 계약을 설명해야 한다. |
| 설치 및 실행 가이드 | Markdown | `docs/submission/install_and_run_guide.md` | Go 설정, CLI 실행, API 실행, mock mode, 기대 출력을 설명해야 한다. |
| 테스트 가이드 | Markdown | `docs/submission/test_guide.md` | Go 테스트, team-validation, 기대 신호, 로그 보존 방법을 설명해야 한다. |

## 6. 개발 가이드 요구사항

개발 가이드와 보조 기록은 다음 내용을 문서화해야 합니다.

- API/CLI 프로토타입 구현을 위한 Go 언어 개발
- 2종 이상 LLM 또는 코딩 에이전트 역할을 이용한 교차 검증
- 정리된 대표 프롬프트 template 기반의 프롬프트 공유 문서
- 로그와 오류 메시지 기반 검증 기록
- 생성 문서, README link, DOCX 존재 여부, 프로토타입 경계 문장에 대한 사람 검토

실제 사용 여부가 확인되지 않은 특정 LLM vendor명이나 coding-agent product명을 임의로 만들지 않습니다. 문서화에는 `Agent A`, `Agent B`, `primary coding agent`, `secondary review agent`와 같은 중립적 역할명을 사용할 수 있습니다.

## 7. 검증 방법

기본 검증 방법은 로컬 Go 실행입니다.

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

기대되는 프로토타입 수준의 검증 신호는 다음과 같습니다.

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

이 검증은 기능 프로토타입 동작을 확인합니다. 운영 성능, 표준 LLM 벤치마크 품질, 실제 GPU VM 프로비저닝을 증명하지는 않습니다.

## 8. 프로토타입 경계

현재 LLM 정책 값은 `config/ops_llm_benchmark.json`에 수동 정의된 프로토타입 정책 기준값입니다. 최종 표준 벤치마크 결과가 아닙니다. 최종 정량 보고를 위해서는 고정 프롬프트, 고정 데이터셋, 반복 가능한 지표, 문서화된 scoring rule을 갖춘 통제된 per-model Ops 평가가 필요합니다.

CPU/GPU VM 배치 로직은 추천 및 배포 계획 생성 프로토타입입니다. 운영 cloud scheduler, Kubernetes scheduler, GPU device plugin, CB-Tumblebug 프로비저닝을 대체하지 않습니다.

기본 `mock` mode는 live Kubernetes cluster를 변경하지 않습니다.
