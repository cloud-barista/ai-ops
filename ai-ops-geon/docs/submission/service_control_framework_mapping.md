# 서비스 제어 프레임워크 매핑

## 연구 범위와 구현

| 연구 범위 | 설계 산출물 | Go 구현 |
| --- | --- | --- |
| AI LLM 운영 관리 | `01_llm_operation_management_design.md` | policy ranking, benchmark status, actual model 분리 |
| 에이전트 등록 관리 | `02_agent_registration_management_prototype.md` | LLM Deployment Planner의 역할, capability, bounded Action 등록 |
| AI 응용 자동화 에이전트 | `03_ai_application_deployment_control_optimization_strategy.md` | 자연어 App 요구 분석, 자원 요구량 결정, Deployment Manifest 생성 |
| CPU/GPU VM 배포·제어 | 동일 산출물 | Go Guard 검증, AppDeploy 요청, 상태 polling과 로그 조회 |
| 안전 경계 | `test_guide.md` | 계약·자원 값·요청 보존·비밀정보 검증, 명시적 retryable 판단 |

그림 A의 상위 서비스 제어 흐름은 `docs/design/main_llm_go_guard_control_flow.md`에서 통합하여 설명합니다. 그림의 `명령 파일(manifest)`는 Kubernetes manifest가 아니라 AppDeploy 공식 `DeploymentManifest`입니다.

## Pipeline

```text
Natural-language App requirement + app_version_id
  -> actual LLM requirement analysis
  -> CPU / memory / GPU / storage / accelerator decision
  -> AppDeploy DeploymentManifest
  -> deterministic Go Guard validation
  -> AppDeploy POST /api/v1/deployments
  -> deployment status polling and log collection
  -> retry recommendation only when AppDeploy marks an error retryable
```

내부 핵심 에이전트는 `AIApplicationAutomationAgent`이며 현재 주 역할은 LLM Deployment Planner입니다. 기존 범용 Action handoff API는 연구 호환용 보조 경로로 유지하며, AppDeploy 연계의 공식 주 경로는 `POST /api/v1/planner/deployments`입니다. 최종 Target과 Runtime Adapter는 Planner가 아니라 AppDeploy가 선택합니다.
