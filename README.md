<h1 align="center">AI-Ops</h1>

<p align="center">AI application deployment and operations for AI-MCMP</p>

<p align="center">
  <a href="https://github.com/cloud-barista/ai-ops/actions/workflows/ci.yml"><img src="https://github.com/cloud-barista/ai-ops/actions/workflows/ci.yml/badge.svg" alt="Validation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache-2.0"></a>
  <a href="docs/getting-started.md"><img src="https://img.shields.io/badge/docs-Getting%20Started-green" alt="Documentation"></a>
</p>

AI-MCMP의 요구사항 분석, 자원 추천, 배포 및 운영 판단을 구현하는 경희대학교 AIOps 프로토타입입니다.

## Components

- [llmop](llmop/): 자연어 요구사항 분석, ApplicationProfile 생성, 분석용 LLM 비교
- [resource_ops](resource_ops/): 자원 정보 정규화, 필수 조건 검사, 후보 점수화 및 추천
- [deployment_agent](deployment_agent/): 배포 판단, Guard 검증, DeploymentPlan과 Revision 관리
- [platform](platform/): 위 기능을 실행·관측과 연결하는 통합 연구 PoC
- [research_policy](poc/research_policy/): 실측 실행 기록을 학습하는 보상 예측 및 판단 보류 알고리즘
- [optimization_research](poc/optimization_research/): 성능 예측, GPU 후보 평가, 판단 추론 효율화와 공개 추적 자료 기반 연구

입출력과 실행 방법은 각 폴더의 README에 정리했습니다.

## Workflow

응용 요구사항을 `ApplicationProfile`로 정리하고, 실행 조건을 만족하는 자원을 추천합니다. 선택한 자원에 대해 Agent와 Guard가 배포 구성을 검사하며, 승인된 계획은 별도 실행 요청을 거쳐 응용 실행으로 이어집니다. 실행 후 관측값은 유지, 확장 또는 축소 판단에 사용합니다.

`Revision`은 승인된 실행 구성의 버전입니다. 계획의 승인, 실제 적용과 관측 결과를 같은 실험에서 추적합니다.

## Research and Deliverables

- **LLM 요구사항 분석:** 자원 조건과 서비스 기준 추출, 분석용 LLM 후보 비교
- **자원 추천:** 관측 정보 정규화, 필수 조건 검사와 후보 점수화
- **배포 및 운영 판단:** 자원 제약 검증, 실행 구성 관리와 성능 피드백 반영
- **학습 정책:** 실측 기록 수집과 변환, 후보별 보상 학습, 신뢰도와 지원 범위에 따른 판단 보류
- **최적화 실험:** 성능·메모리 예측, CPU/GPU 구성 비교, 예측 재사용과 묶음 처리, 공개 워크로드 분석

구현 코드뿐 아니라 관련 테스트, 설계/API 문서, 재현 스크립트와 실험 보고서를 함께 관리합니다. [산출물 안내](docs/deliverables.md)에서 역할별 자료를 찾을 수 있습니다.

## Quick Start

Go 서비스는 각 폴더의 `go.mod`를 기준으로 독립 실행합니다. 통합 PoC는 Node.js, Python과 별도 AppDeploy 실행 모듈을 사용합니다.

```bash
git clone https://github.com/cloud-barista/ai-ops.git
cd ai-ops
go -C llmop/go/service-control-api test ./...
```

## Documentation

- [설치와 실행](docs/getting-started.md)
- [구성 요소와 연동 구조](docs/architecture.md)
- [주요 활용 시나리오](docs/use-cases.md)
- [검증 범위와 결과](docs/validation.md)
- [역할별 코드와 산출물](docs/deliverables.md)
- [공개 파일과 별도 준비 자료](docs/source-inventory.md)

## Status

연구용 프로토타입입니다. 로컬 기능 시험, AWS CPU/GPU 배포 검증, 학습 정책의 오프라인 평가를 구분합니다. 기관 간 정식 인터페이스 연동은 별도 협의 대상입니다.

`poc`에는 플랫폼에 연결된 학습 정책과 별도 오프라인 연구가 함께 있습니다. 실험 보고서에는 채택한 방법과 개선 기준을 통과하지 못한 방법을 모두 기록했습니다. 모든 연구 방법이 플랫폼의 기본 정책으로 적용된 것은 아닙니다.

인증 정보, 개인 환경 설정, 모델 가중치, 원본 실험 DB는 포함하지 않습니다. 클라우드 실행은 기본 비활성 상태입니다.

## Contributing and License

[기여 방법](CONTRIBUTING.md) / [Apache-2.0](LICENSE)
