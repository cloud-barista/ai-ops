<h1 align="center">AI-Ops</h1>

<p align="center">AI application deployment and operations for AI-MCMP</p>

<p align="center">
  <a href="https://github.com/cloud-barista/ai-ops/actions/workflows/ci.yml"><img src="https://github.com/cloud-barista/ai-ops/actions/workflows/ci.yml/badge.svg" alt="Validation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="Apache-2.0"></a>
  <a href="docs/getting-started.md"><img src="https://img.shields.io/badge/docs-Getting%20Started-green" alt="Documentation"></a>
</p>

AI-Ops는 AI-MCMP의 AI 응용 요구사항 분석, 자원 추천, 배포 및 운영 판단을 개발하는 경희대학교 AIOps 프레임워크입니다. 자연어로 입력한 실행 조건을 구조화하고, 적합한 자원과 실행 구성을 제안합니다. 연구용 PoC에서는 이 판단을 실제 응용 배포와 관측까지 연결해 검증합니다.

핵심 판단 모듈은 Go API로 제공하며, 통합 웹 PoC와 Python 연구 코드를 함께 관리합니다. 현재 공개 버전은 연구용 프로토타입입니다.

## Contents

- [주요 기능](#features)
- [동작 구조](#workflow)
- [구성 요소](#components)
- [설치와 실행](#quick-start)
- [사용 예시](#usage)
- [연구 알고리즘](#research)
- [개발 현황과 로드맵](#development-status)
- [검증](#validation)
- [문서](#documentation)
- [기여와 라이선스](#contributing-and-license)

## Features

- **요구사항 분석:** 자연어 또는 구조화된 App Spec에서 CPU, 메모리, 가속기, 복제본 범위와 서비스 성능 조건을 추출합니다. 분석용 LLM 후보 선택과 결과 검증을 지원합니다.
- **자원 추천:** 자원 관측 정보를 정규화하고, 필수 조건을 만족한 후보를 점수화해 추천 사유와 함께 반환합니다.
- **배포 계획:** 요구사항과 선택 자원을 바탕으로 배포 가능 여부를 판단합니다. Guard가 자원 제약과 허용 작업을 검사하고, 검증된 실행 구성을 Revision으로 관리합니다.
- **운영 판단:** 실행 후 성능과 자원 사용률을 받아 복제본 유지, 확장 또는 축소를 판단합니다. 변경 계획과 실제 적용 상태를 구분합니다.
- **실행 연계:** PoC 어댑터로 응용 등록, 로컬 또는 AWS VM 실행, 실제 추론, 관측과 종료를 연결합니다.
- **실험 추적:** 요구사항부터 추천, 계획, 실행과 운영 판단을 동일한 Flow에 기록합니다. 실험 결과와 개별 실행 이력을 확인할 수 있습니다.

## Workflow

```mermaid
flowchart LR
    U[사용자 요청 또는 App Spec] --> L[LLM_Op 요구사항 분석]
    L -->|ApplicationProfile| R[Resource Ops 자원 추천]
    L --> D[Deployment Agent]
    R -->|ResourceRecommendation| D
    D --> G[Guard 검증]
    G --> P[승인된 실행 구성과 Revision]
    P -->|별도 실행 요청| A[PoC 실행 어댑터와 AppDeploy]
    A --> V[로컬 프로세스 또는 AWS VM]
    V --> O[실행 상태와 성능 관측]
    O --> J[운영 판단]
    J -->|변경이 필요한 경우| G
```

`Revision`은 실행 구성의 버전입니다. 예를 들어 목표 복제본을 1개에서 2개로 변경한 구성을 새 Revision으로 기록할 수 있습니다. 버전을 기록한 뒤에는 별도 적용 요청과 실행 상태 확인이 필요합니다.

이 흐름은 경희대 알고리즘을 시험하기 위한 PoC 실행 구조입니다. 기관 간 정식 연동에서는 합의한 인터페이스를 통해 판단 결과를 전달하도록 개발합니다. 자세한 책임 범위는 [구성 요소와 연동 구조](docs/architecture.md)에 정리했습니다.

## Components

- **[llmop](llmop/)**: 요구사항 분석과 `ApplicationProfile` 생성, 분석용 LLM 선택 및 비교
- **[resource_ops](resource_ops/)**: 자원 정규화, 조건 검사, 후보 점수화와 `ResourceRecommendation` 생성
- **[deployment_agent](deployment_agent/)**: Agent 등록과 호출, 배포 및 운영 판단, Guard 검증과 실행 구성 관리
- **[platform](platform/)**: 역할별 API, 외부 실행 모듈과 Prometheus를 연결하는 웹 PoC
- **[poc/research_policy](poc/research_policy/)**: 실측 기록 수집, 후보별 보상 학습과 불확실한 판단의 보류
- **[poc/optimization_research](poc/optimization_research/)**: 성능 예측, GPU 구성 평가, 판단 추론 효율화와 공개 워크로드 분석

각 폴더의 README에서 API, 실행 방법과 테스트를 확인할 수 있습니다. [산출물 안내](docs/deliverables.md)는 역할별 코드, 설계 문서와 실험 보고서의 위치를 설명합니다.

## Quick Start

### 준비 사항

- Git, Go 1.26.5, Node.js 22 이상, Python 3.10 이상
- 자연어 분석: Ollama 또는 설정된 LLM provider
- 실제 응용 실행: 별도 AppDeploy 모듈과 모델 실행 환경
- 통합 관측 및 학습 정책: Prometheus, Python 의존성, 별도로 준비한 실측 DB와 학습 산출물

기능별 의존성이 다릅니다. Go API와 단위 검사는 원본 실측 DB 없이도 실행할 수 있습니다. 모델 파일과 원본 DB는 저장소에 포함하지 않습니다.

```bash
git clone https://github.com/cloud-barista/ai-ops.git
cd ai-ops
```

### 요구사항 분석 API 실행

```bash
go -C llmop/go/service-control-api run ./cmd/llmop-api
```

기본 주소는 `http://127.0.0.1:8080`입니다. `/healthz`에서 서비스 상태를, `/openapi.yaml`에서 API 명세를 확인합니다. 구조화 입력은 외부 LLM 호출 없이 사용할 수 있습니다.

자원 추천과 배포 판단 API는 독립적으로 실행할 수 있습니다. 동시 실행 시 포트와 API 연결 주소를 설정해야 합니다. [독립 서비스 실행](docs/getting-started.md#독립-서비스)을 참고하세요.

### 통합 PoC 실행

화면과 플랫폼 API만 확인하려면 다음 명령을 사용합니다.

```bash
node platform/server/index.mjs
```

웹 주소는 `http://127.0.0.1:18100/`입니다. 요구사항 분석, 자원 추천과 실제 배포까지 사용하려면 각 API와 실행 모듈도 준비해야 합니다.

Windows 통합 실행은 AppDeploy, 모델 실행 환경, Prometheus와 연구 정책을 준비한 뒤 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File platform/scripts/Run-KhuResearchPoc.ps1 -Action Start -AppDeployModule "C:\path\to\AppDeploy"
```

준비 순서와 설정은 [설치와 실행](docs/getting-started.md)에 있습니다. 시작 명령은 VM을 생성하지 않습니다. 실제 클라우드 실행은 연결 설정, 비용 검토, 실행 승인과 준비 검사를 거칩니다.

## Usage

요구사항 분석 API를 실행한 상태에서 저장소 루트의 별도 터미널로 요청합니다. 아래 명령은 Bash 기준입니다.

```bash
curl -sS http://127.0.0.1:8080/api/v1/application-profiles \
  -H 'Content-Type: application/json' \
  --data-binary @llmop/examples/application-profile/structured-request.json
```

예시 입력은 배치 임베딩 응용에 CPU 8코어, 메모리 32GiB, 저장소 50GiB, 복제본 1~2개를 지정합니다. 응답의 `application_profile`에는 정규화한 실행 조건이, `analysis.mode`에는 `structured`가 기록됩니다. 이 요청은 프로파일을 생성하는 단계입니다.

자연어 분석은 [입력 예시](llmop/examples/application-profile/generate-request.json)와 [LLM 후보 설정](llmop/config/application_profile_llm_candidates.json)을 사용합니다. 분석에 사용하는 LLM과 실제 배포할 응용 모델은 별도로 선택합니다.

웹 PoC에서는 다음 순서로 진행합니다.

1. **요구사항:** 분석용 LLM을 선택하고 응용 요구사항을 분석합니다.
2. **자원 추천과 선택:** 조건 충족 여부와 후보 점수를 확인하고 실행 대상을 선택합니다.
3. **배포 계획:** Agent 판단과 Guard 검증 결과를 확인합니다.
4. **배포 실행:** 승인된 구성을 별도로 실행하고 응용의 `RUNNING` 상태를 확인합니다.
5. **모니터링과 운영 판단:** 실제 추론과 관측을 수행하고 유지, 확장 또는 축소 판단을 확인합니다.
6. **실험 결과와 실행 기록:** 같은 Flow의 단계별 결과를 확인하고 시험 자원을 종료합니다.

각 버튼의 조건과 동작은 [PoC 버튼 안내](platform/docs/POC_BUTTON_GUIDE.md)에 정리했습니다.

## Research

경희대 연구는 응용 컨텍스트를 자원 조건과 실행 판단으로 변환하고, 실행 피드백으로 판단을 개선하는 데 초점을 둡니다.

- **현재 자원 추천:** 필수 자원 조건으로 후보를 걸러낸 뒤 정규화한 지표의 가중 점수로 순위를 결정합니다. [점수 산식](resource_ops/docs/formulas.md)을 공개합니다.
- **학습 기반 후보 평가:** 실측 실행 기록으로 후보별 보상을 예측합니다. 자원 제약을 만족한 후보 중 예측 효용이 높은 구성을 선택하고, 신뢰도와 학습 범위가 부족하면 판단을 보류합니다. [Research Policy](poc/research_policy/)에서 구현과 평가 방법을 확인할 수 있습니다.
- **판단 추론 효율화:** 예측 결과 재사용, 후보 묶음 처리와 병렬 처리의 시간 및 결과 일치성을 평가합니다. 성능과 메모리 예측, GPU 구성 비교, 공개 추적 자료 분석도 [최적화 연구](poc/optimization_research/)에서 관리합니다.

학습 정책의 오프라인 replay와 shadow 평가는 실제 VM을 변경하지 않습니다. 연구 보고서에는 개선 기준을 통과하지 못한 방법도 포함하며, 모든 방법을 플랫폼의 기본 정책으로 채택한 것은 아닙니다.

## Development Status

기준일: **2026-10-19**. 아래 완료 표시는 PoC 구현과 명시한 시험 범위를 뜻합니다.

### 현재 구현과 검증

- [x] 요구사항 분석, 자원 추천, 배포 판단의 역할별 API 분리
- [x] 자연어 및 구조화 입력, 분석용 LLM 선택, 조건 검사와 후보 점수화
- [x] Agent 등록과 허용 작업 검사, Guard 검증, 계획과 Revision 관리
- [x] 통합 PoC에서 요구사항부터 실행, 관측과 운영 판단까지 연결
- [x] 로컬 CPU와 AWS CPU/GPU VM의 응용 실행 및 종료 검증
- [x] 실측 기반 학습 정책 구현, 오프라인 평가와 판단 효율화 실험

실제 VM 수 변경은 별도 CPU 시험에서 명시적 적용으로 확인했습니다. 자동 정책에 의한 GPU 증감, TPU 실행과 기관 정식 시스템 연동은 완료 범위에 포함하지 않습니다. [검증 범위와 결과](docs/validation.md)에서 시험별 조건을 확인하세요.

### 2차년도 개발 예정

경희대 Kick-off 발표자료의 2차년도 항목을 기준으로 다음 작업을 관리합니다.

- [ ] GPU/TPU VM 기반 응용 배포 자동화와 업데이트 기술
- [ ] GPU/TPU 기반 응용 요구 분석 및 추천 프로토타입
- [ ] Agent별 프레임워크 접근 권한 관리
- [ ] Agent 라이프사이클 관리
- [ ] 자연어 지원 Agent 프로토타입
- [ ] GPU/TPU VM 배포와 제어에 특화된 추론 최적화 실행

현재 구현을 각 항목의 출발점으로 활용합니다. 요구사항 정의서의 연차별 항목과 기관 간 연동 과제는 [경희대 개발 현황과 로드맵](docs/roadmap.md)에 출처, 현재 수준과 남은 개발 내용으로 나누어 정리했습니다.

## Validation

Go 서비스는 각 모듈에서 검사합니다. 예를 들어:

```bash
go -C llmop/go/service-control-api test ./...
go -C llmop/go/service-control-api vet ./...
```

플랫폼 전체 검사는 Windows와 PowerShell을 사용합니다. 아래 명령은 Windows의 Git Bash 기준이며, Windows 전용 mutex 검사가 포함되어 있습니다.

```bash
python -m pip install -r platform/requirements-test.txt
node --test platform/tests/*.test.mjs
python -m unittest discover -s platform/tests -p 'test_*.py'
python -m unittest discover -s platform/runtime-vm -p 'test_*.py'
```

GitHub CI는 Go 4개 모듈, 플랫폼과 데이터 없이 실행 가능한 연구 검사를 수행합니다. 실제 모델, 원본 DB 또는 클라우드가 필요한 실험은 별도 환경에서 수행하며 CI 결과와 구분합니다. 전체 검사와 해석상 제한은 [검증 문서](docs/validation.md)를 참고하세요.

## Documentation

- [설치와 실행](docs/getting-started.md)
- [구성 요소와 연동 구조](docs/architecture.md)
- [주요 활용 시나리오](docs/use-cases.md)
- [경희대 개발 현황과 로드맵](docs/roadmap.md)
- [검증 범위와 결과](docs/validation.md)
- [역할별 코드와 산출물](docs/deliverables.md)
- [공개 파일과 별도 준비 자료](docs/source-inventory.md)

상세 API와 초기 설계 문서는 각 컴포넌트의 `docs`에서 관리합니다. 과거 실험 보고서의 결과는 해당 보고서에 명시한 버전과 실행 조건을 기준으로 해석합니다.

## Contributing and License

기능 개선, 오류 제보와 문서 보완은 [기여 방법](CONTRIBUTING.md)을 참고하세요. 이 프로젝트는 [Apache-2.0](LICENSE) 라이선스를 따릅니다. 인증 정보와 개인 환경 설정은 저장소에 등록하지 않습니다.

관련 프로젝트: [AI-MCMP](https://github.com/cloud-barista/ai-mcmp), [CB-Tumblebug](https://github.com/cloud-barista/cb-tumblebug), [CB-Spider](https://github.com/cloud-barista/cb-spider)
