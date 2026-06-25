# Requirements Definition

## Functional Requirements

| ID | Requirement | Status |
| --- | --- | --- |
| FR-01 | Go 기반 Ops LLM 선정 API/CLI를 제공한다. | implemented |
| FR-02 | Go 기반 Agent registry 조회와 bounded action 검증을 제공한다. | implemented |
| FR-03 | CPU/GPU VM 후보를 SLO, 처리량, 비용, 용량 기준으로 평가한다. | implemented |
| FR-04 | 선택된 VM에 대한 Kubernetes deployment/control plan을 생성한다. | implemented |
| FR-05 | LLM 선정, 배치 추천, manifest dry-run, Agent review를 통합한 readiness report를 생성한다. | implemented |

## Non-Functional Requirements

| ID | Requirement |
| --- | --- |
| NFR-01 | 제출/시연 공통 경로의 개발 언어는 Go로 유지한다. |
| NFR-02 | cluster-specific real execution은 공통 검증에서 제외한다. |
| NFR-03 | 외부 framework, 후처리 도구, 비핵심 legacy 계층은 제출 패키지의 핵심 구현에 포함하지 않는다. |
| NFR-04 | 설정 파일은 재현 가능한 JSON 계약으로 유지한다. |

## Deliverables

| Deliverable | Location |
| --- | --- |
| LLM 운영관리 구조 설계 | `docs/design/ops_llm_selection_guide.md`, `docs/design/research_task_integration_design.md` |
| 에이전트 등록관리 프로토타입 | `config/agent_registry.json`, `go/service-control-api` |
| AI 응용 배포/제어 추론 최적화 전략 | `config/inference_optimization.json`, `docs/design/inference_optimization_guide.md`, `docs/design/ai_application_deployment_strategy.md` |
| 실행 가능한 API/CLI | `go/service-control-api` |
| bounded action guard | `go/aiops-guard` |
