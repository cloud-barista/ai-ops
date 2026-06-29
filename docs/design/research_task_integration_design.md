# 연구 과제 통합 설계

## 대상 범위

통합 연구 과제는 AI service-control and management automation framework의 Go 기반 prototype으로 구현되어 있습니다. 1차년도 service-control layer에 배정된 산출물에 초점을 둡니다.

| 연구 항목 | Prototype 구현 |
| --- | --- |
| Ops 분석 및 최적 LLM 선정 | `go/service-control-api` LLM selection logic |
| AI LLM 운영 관리 구조 | service-operations readiness pipeline |
| AI agent registration management | `config/agent_registry.json`과 Go API/CLI validation |
| CPU/GPU VM 기반 AI 응용 배포·제어 | CPU/GPU 배치 및 Kubernetes 배포 계획 생성 |
| 안전 경계 | `go/aiops-guard` standalone bounded-action validator |

## System Flow

```text
Ops policy/config
-> Go LLM selection
-> Agent registry and bounded-action validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment/control plan
-> manifest dry-run and guard-readiness check
-> service-operations readiness report
```

## AI-Infra 경계

CB-Tumblebug 또는 다른 AI-Infra component는 외부 VM provisioning 및 management infrastructure로 취급합니다. 이 프로젝트는 해당 시스템을 대체하지 않습니다. 설정에서 CPU/GPU VM resource assumption을 소비하고, 그 infrastructure boundary 위에서 AI application placement와 deployment-control decision을 생성합니다.

## 개발 경계

제출/시연 경로에는 핵심 범위 밖 experiment runner, external agent orchestration framework, provider-specific monitoring adapter, local cluster helper script를 포함하지 않습니다. 이를 통해 Go 개발 언어 요구사항과 담당 연구 범위에 맞게 구현을 유지합니다.
