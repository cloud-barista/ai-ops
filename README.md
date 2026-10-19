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

입출력과 실행 방법은 각 폴더의 README에 정리했습니다.

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

## Status

연구용 프로토타입입니다. 로컬 기능 시험, AWS CPU/GPU 배포 검증, 학습 정책의 오프라인 평가를 구분합니다. 기관 간 정식 인터페이스 연동은 별도 협의 대상입니다.

인증 정보, 개인 환경 설정, 모델 가중치, 원본 실험 DB는 포함하지 않습니다. 클라우드 실행은 기본 비활성 상태입니다.

## Contributing and License

[기여 방법](CONTRIBUTING.md) / [Apache-2.0](LICENSE)
