# Integration Boundary

## 목적

이 문서는 geon service-control prototype과 AppDeployer 또는 AI-MCMP 연계 프레임워크 사이의 책임 경계를 정리합니다.

## Service-Control 책임

geon service-control layer는 다음 판단과 계획 생성을 담당합니다.

| 영역 | 책임 |
| --- | --- |
| Ops LLM | LLM 후보 설정, scenario 기반 평가, 실행 결과 요약 |
| Agent registry | 등록 agent와 bounded action 검증 |
| Placement decision | CPU/GPU VM 후보 중 워크로드 요구사항에 맞는 배치 추천 |
| Deployment/control plan | AI 응용 배포·제어 계획과 manifest 초안 생성 |
| Guard validation | 허용 action과 운영 제어 경계 검증 |

이 layer는 판단과 계획을 생성하지만, 실제 운영 플랫폼에 대한 live deployment 완료를 직접 주장하지 않습니다.

## 연계 프레임워크 책임

AppDeployer 또는 AI-MCMP 연계 프레임워크는 다음 실행 계층을 담당합니다.

| 영역 | 책임 |
| --- | --- |
| 실제 배포 실행 | application deployment, update, rollback apply |
| Infra 연계 | GPU VM, Kubernetes cluster, provider credential, runtime endpoint 제공 |
| 상태 반영 | 실제 배포 결과, runtime 상태, endpoint health 제공 |
| Credential 관리 | provider API key, kubeconfig, cloud credential 관리 |

service-control layer는 이 실행 계층에서 제공하는 endpoint, infra 상태, credential reference를 config 또는 API 입력으로 소비합니다.

## LLM Endpoint 경계

Ollama는 통합 필수 요소가 아니라 local example provider입니다. 통합 환경에서는 다음과 같은 endpoint로 교체할 수 있습니다.

- vLLM
- LM Studio
- OpenAI API
- Azure OpenAI
- 연구용 GPU 서버 endpoint
- AI-MCMP 또는 deployment platform이 제공하는 OpenAI-compatible LLM endpoint

service-control layer는 OpenAI-compatible endpoint 계약만 사용하므로, provider 교체는 candidate config 변경으로 처리합니다.

## Deployment 경계

geon prototype은 Kubernetes manifest generation, mock mode, dry-run boundary를 통해 배포 계획을 검증합니다. 실제 Kubernetes live deployment, update 완료, rollback 완료는 AppDeployer 또는 연계 프레임워크의 실행 결과가 있을 때만 주장할 수 있습니다.

## 주장 가능한 결과

| 주장 | 조건 |
| --- | --- |
| service-control 판단 검증 완료 | Go test, team-validation, validate-system 성공 |
| 실제 LLM 응답 평가 완료 | `evaluation_summary.json`에 `benchmark_status = executed` 기록 |
| VM 환경 검증 완료 | VM 내부에서 `validate-system --target vm` 실행 및 evidence 저장 |
| 실제 배포 완료 | AppDeployer 또는 AI-MCMP 연계 프레임워크의 실행 결과 확보 |

이 구분을 통해 service-control prototype의 판단 결과와 외부 platform 실행 결과를 혼동하지 않습니다.
