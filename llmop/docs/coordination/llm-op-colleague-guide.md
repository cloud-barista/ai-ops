# LLM_Op 개발 인계 가이드

> 이 문서는 `cloud-barista/ai-ops` 저장소의 `LLM_Op` 작업을 동료 연구원 또는 동료 AI에게 인계하기 위한 단일 진입 문서이다.
>
> 문서를 GPT와 함께 읽을 때는 파일을 첨부한 뒤 다음과 같이 요청하면 된다.
>
> **"첨부한 `llm-op-colleague-guide.md`를 읽고, LLM_Op이 구현한 기능과 안전 경계, 주요 파일, 시연 방법, geon 브랜치에 병합할 때 확인할 사항을 내 역할에 맞게 설명해 줘."**

## 1. 먼저 확인할 GitHub 위치

- 저장소: <https://github.com/cloud-barista/ai-ops>
- 작업 브랜치: <https://github.com/cloud-barista/ai-ops/tree/LLM_Op>
- `geon`과 변경 비교: <https://github.com/cloud-barista/ai-ops/compare/geon...LLM_Op>
- 병합용 Draft PR: <https://github.com/cloud-barista/ai-ops/pull/1>
- 이 문서의 geon 최신화 기준: `geon@1999d79`, `LLM_Op@59c8a9b` 이후 통합 변경
- 기존 원격 CI 성공 증적: <https://github.com/cloud-barista/ai-ops/actions/runs/31154048774> (최신 통합 커밋은 새 CI로 재검증)

현재 PR 방향은 `LLM_Op → geon`이다. 초기 4개 LLM_Op 커밋과 최신 geon 통합·Guard-first 계약 변경이 하나의 안전 파이프라인을 구성하므로 마지막 커밋만 따로 cherry-pick하지 말고 PR 전체를 검토한다.

## 2. 한 문장 요약

`LLM_Op`은 **사용자의 자연어 운영 요청과 외부에서 전달받은 서버 상태·로그·모니터링 정보를 안전하게 정규화하고, 두 단계 LLM의 구조화된 출력을 결정적 Guard로 검증한 뒤, AppDeploy 담당자가 받을 수 있는 `DeploymentCreateRequest` 초안을 만드는 계층**이다.

실제 LLM API 과금, 실제 AppDeploy 호출 및 실제 배포는 수행하지 않는다.

통합의 기준 순서는 **LLM_Op 최초 Safeguard → geon의 DEPLOY 판단과 canonical `INITIAL` revision → LLM_Op AppDeploy prepare-only 투영**이다. 별도 구현을 만들더라도 이 순서, allow/clarify/reject action, fail-closed 상태와 금지 필드 계약을 호환 기준으로 삼는다. 세부 규범은 `llm-op-guard-first-integration-standard.md`에 있다.

## 3. 처리 흐름

```text
사용자 자연어 요청 + 상태/로그/모니터링
  ↓
결정적 Request Guard
  - prompt injection, secret, 명령 실행, 책임 범위, 문법 검사
  ↓
Operation Context 정규화
  - freshness, 시각 관계, 입력 상한, 비밀정보 제거
  ↓
LLM ① Safeguard Review
  - allow_request / request_clarification / reject_request
  ↓ allow_request일 때만 진행
geon Requirement/Profile/Recommendation/DEPLOY Guard
  ↓
canonical Flow + DesiredDeploymentSpec + INITIAL revision
  ↓ 승인된 초기 Flow와 최초 Safeguard binding 검증
LLM ② Manifest Proposal
  - action, reason, confidence, CPU/메모리/GPU/스토리지 제안
  ↓
Strict JSON + 의미·자원·readiness 검증
  ↓
Go 코드가 신뢰 가능한 ID와 검증된 제안을 결합
  ↓
AppDeploy DeploymentCreateRequest 초안
  - HANDOFF_READY
  - submission_status: not_submitted
```

### 중요한 신뢰 경계

1. LLM 출력은 항상 신뢰하지 않는 입력이다.
2. LLM ①이 허용해야만 LLM ②가 실행될 수 있다.
3. LLM ②는 shell 명령, cloud provider, credential, endpoint, target 또는 완성된 배포 명령을 선택할 권한이 없다.
4. LLM ②가 만드는 것은 최종 Manifest가 아니라 제한된 **Manifest Proposal**이다.
5. 최종 AppDeploy 요청은 Go 코드가 생성하며, 실제 POST는 하지 않는다.
6. 브라우저 시연기의 검증 코드는 설명·시연용 mirror다. 권위 있는 검증 구현은 Go의 `internal/llmop`이다.

## 4. 구현 파일 구조

```text
ai-ops/
├─ docs/
│  ├─ llm-op/                     # 설계, 계약, 시연, Adapter 문서
│  └─ coordination/               # geon 작업자와의 협업 문서
├─ schemas/llm-op/                # 두 LLM 출력 JSON Schema
├─ examples/llm-op/               # AI 서비스, 47개 시나리오, golden fixture
├─ go/service-control-api/
│  ├─ internal/llmop/             # 핵심 Guard 및 planning pipeline
│  ├─ internal/llmopbridge/       # geon Common JSON 연결 계층
│  ├─ cmd/llmop-demo/             # 오프라인 CLI 시연
│  └─ internal/webui/
│     ├─ static/llm_op_demo.*     # 브라우저 수동 시연 페이지
│     └─ llm_op_demo_test.js      # 브라우저 계약 테스트
├─ scripts/validate-llmop-demo-data.ps1
├─ open-llm-op-demo.cmd           # 서버 없는 Windows 시연 실행기
└─ .github/workflows/ci.yml
```

### 4.1 설계와 계약 문서

문서의 시작점은 `docs/llm-op/README.md`이다.

| 파일 | 역할 |
|---|---|
| `00-existing-assets-audit.md` | 기존 geon, AppDeployer, 시나리오 및 API 자산 조사 |
| `01-contract.md` | `v1alpha1` 입력·출력, 상태 코드, AppDeploy 인계 계약 |
| `02-delivery-plan.md` | 완료 조건, 통합 불변식, live 연결 전 과제 |
| `03-common-json-bridge.md` | geon Common JSON을 LLM_Op Request로 투영하는 규칙 |
| `04-implementation-completeness-audit.md` | 구현 완성도와 의도적으로 제외한 경계 감사 |
| `05-safeguard-policy-llm-guide.md` | 위협 모델, 안전 규칙, 두 LLM 프롬프트와 검토 체크리스트 |
| `06-demo-scenarios.md` | 47개 요청 시나리오와 AI 서비스 설명 |
| `07-manual-two-stage-browser-demo.md` | 서버 없는 복사·붙여넣기 시연 절차 |
| `08-operation-context-adapter-contract.md` | 외부 상태·로그 Adapter의 입력 형식과 책임 경계 |

협업용 문서는 `docs/coordination/`에 있다.

- `to-geon-llm-op-handoff.md`: 양쪽 작업 범위, 제공 계약, 충돌 회피 및 검토 요청
- `llm-op-guard-first-integration-standard.md`: 별도 구현도 따라야 할 최초 Safeguard 순서, 단일 권위와 금지 fallback 기준
- `geon-bug-notes.md`: 통합 과정에서 발견한 잠재 결함과 계약 위험
- `geon-draft-pr-message.md`: Draft PR 설명의 원본
- `llm-op-colleague-guide.md`: 현재 읽고 있는 단일 인계 문서

### 4.2 출력 Schema

`schemas/llm-op/`에는 다음 두 계약이 있다.

- `safeguard-review.v1alpha1.schema.json`: 첫 번째 LLM의 결정과 근거 형식
- `manifest-proposal.v1alpha1.schema.json`: 두 번째 LLM의 제한된 작업·자원 제안 형식

Parser는 알려지지 않은 필드, 중복 JSON key, trailing data, 과도한 숫자, 허용되지 않은 enum 및 잘못된 completion identity를 거부한다.

### 4.3 예제와 시나리오

`examples/llm-op/`에는 다음 자료가 있다.

- `ai-service-catalog.json`: 대화형 Q&A, intent 분류, embedding, document VLM의 AI 서비스 4종
- `user-input-scenarios.json`: 13개 범주의 사용자 요청 계약 시나리오 47건
- `fresh-latency-request.json`: 자연어 요청과 최신 상태·로그·metric 입력
- `fresh-latency-safeguard-review.json`: LLM ① allow 예시
- `fresh-latency-qwen-proposal.json`: LLM ② create proposal 예시
- `fresh-latency-appdeploy-request.expected.json`: 예상 AppDeploy 요청의 golden 결과

47건은 모두 정적 계약 catalog이며, 브라우저에서는 그중 대표적인 성공·거부·보완 요청 흐름 8건을 실행할 수 있다.

시연 catalog의 `qwen3.5:4b`는 사용할 수 있는 모델의 intended metadata다. 모델이 실제로 로드되었거나 호출되었다는 증거로 사용하지 않는다. 모델 선택과 ranking도 이 브랜치의 책임이 아니며 caller가 `candidate_id`를 고정한다.

### 4.4 Go 핵심 구현

`go/service-control-api/internal/llmop/`의 주요 파일은 다음과 같다.

| 파일 | 역할 |
|---|---|
| `models.go` | Request, OperationContext, Proposal, Result, Evidence, Handoff 모델 |
| `request_guard.go` | LLM 전 prompt injection, secret, 명령, 범위, 자원 문법 검사 |
| `normalizer.go` | freshness, 시각 관계, 입력 상한, 정렬, redaction |
| `safeguard_harness.go` | LLM ① 프롬프트·출력 검증 및 다음 단계 제어 |
| `safeguard_stage.go` | 최초 승인 continuation과 요청·관측·policy·candidate SHA-256 binding |
| `planner.go` | LLM ② 프롬프트, strict JSON, 제안 검증 및 request 생성 |
| `semantic_guard.go` | 사용자 자원 의도, 추천값, runtime readiness의 정확한 일치 검증 |
| `provider.go` | caller-pinned provider 경계, live completion 기본 차단, split live Go integration |

동일 디렉터리의 `*_test.go`들은 injection, secret, freshness, strict JSON, 자원 상한, ID 유출, completion envelope, 성공 fixture와 실패 상태를 검증한다.

### 4.5 geon 연결 계층

`go/service-control-api/internal/llmopbridge/bridge.go`는 기존 pre-decision fixture 체인을 투영하고, `approved_flow.go`의 `ProjectApprovedInitialFlow`는 최초 Safeguard와 geon의 승인된 `INITIAL` revision을 결합하는 기준 구현이다. `approved_flow_test.go`에는 Safeguard → 승인 Flow bridge → Proposal의 offline 종단 및 drift 회귀 검사가 있다. 실제 route/orchestrator 호출부는 아직 연결하지 않았다.

```text
application.analysis.request
  → application.context.created
  → resource.recommendation.created
  → llmop.Request
```

기존 geon 흐름이나 `deploymentplanner.GenerateInput`을 변경하지 않았다. 현재 AppDeploy 계약으로 손실 없이 표현하기 어려운 topology, SLO, cost, multi-replica 및 GPU device-memory 요구는 조용히 버리지 않고 fail-closed로 거부한다.

주의: 현재 geon `LocalRequirementAnalyzer`는 replica 미지정을 assumption으로 남기고 `GPU 0`도 GPU-required로 해석한다. 따라서 새 approved bridge의 성공 fixture는 계약 수준의 명시 Profile이며 실제 local analyzer CPU/GPU 종단 성공을 증명하지 않는다. 이 차이는 `geon-bug-notes.md` 항목 8에 재현 조건으로 기록했다.

### 4.6 시연 구현

- `cmd/llmop-demo/`: 저장된 fixture와 고정 시각으로 전체 pipeline을 실행하는 오프라인 CLI
- `internal/webui/static/llm_op_demo.html`: 단계 설명과 입력·출력을 보여주는 브라우저 화면
- `llm_op_demo_contract.js`: 8개 대표 시나리오, prompt, strict JSON 및 preview 생성
- `llm_op_demo.js`: 복사, 붙여넣기, 단계 잠금, 결과 표시와 호출 원장
- `llm_op_demo_test.js`와 `manifest_stages_test.js`: dependency-free Node 계약 테스트 11건
- `open-llm-op-demo.cmd`: Go 서버 없이 HTML을 기본 브라우저로 여는 launcher

## 5. 동료가 직접 시연하는 방법

### 5.1 브랜치 받기

```powershell
git clone https://github.com/cloud-barista/ai-ops.git
cd ai-ops
git fetch origin
git switch -c LLM_Op --track origin/LLM_Op
```

이미 로컬 브랜치가 있다면 다음을 사용한다.

```powershell
git switch LLM_Op
git pull --ff-only origin LLM_Op
```

### 5.2 권장: 서버 없는 시연

저장소 루트에서 실행한다.

```powershell
.\open-llm-op-demo.cmd
```

시연 순서는 다음과 같다.

1. 화면에서 8개 대표 시나리오 중 하나를 선택한다.
2. 자연어 요청과 상태·로그 입력을 확인하고 시연을 시작한다.
3. Safeguard의 SYSTEM/USER prompt를 복사한다.
4. 외부 AI 서비스의 raw JSON 응답을 붙여넣거나 `예시 응답 넣기`를 사용한다.
5. Safeguard가 `allow_request`를 반환하고 검증을 통과해야 Proposal 단계가 열린다.
6. Proposal prompt는 새롭고 독립된 AI 대화에 입력한다.
7. 두 번째 raw JSON 응답을 붙여넣거나 예시 응답을 사용한다.
8. 최종 `HANDOFF_READY_NOT_SUBMITTED`와 AppDeploy `prepared_request`를 확인한다.

예시 응답만 사용하면 Go 서버, 모델 API, AppDeploy API 호출은 모두 0회다. 외부 AI 서비스를 사용한 경우에만 해당 서비스의 비용·보관 정책이 적용된다.

### 5.3 Go 서비스 route로 시연

```powershell
.\run-agent-control.cmd
```

- 상태 확인: <http://127.0.0.1:18080/healthz>
- 시연 페이지: <http://127.0.0.1:18080/llm-op-demo>

환경변수 없이 모듈에서 서비스를 직접 실행하면 기본 포트는 `8080`이다. GitHub에 브랜치를 push한 것만으로 시연 페이지가 인터넷에 호스팅되는 것은 아니다.

## 6. geon에 병합하는 방법

권장 병합 경로는 기존 Draft PR #1이다. `geon`에 직접 push하거나 최신 커밋 하나만 cherry-pick하지 않는다.

1. PR에서 문서, Schema, `internal/llmop`, `internal/llmopbridge`를 우선 검토한다.
2. geon 쪽에서 공식 HTTP integration 지점과 AppDeploy 인계 책임을 합의한다.
3. 병합 직전에 `geon`이 변경되었다면 `LLM_Op`에 최신 base를 merge한다.

```powershell
git fetch origin
git switch LLM_Op
git merge origin/geon
# 충돌을 해결하고 검증한다.
git push origin LLM_Op
```

4. GitHub Actions가 다시 통과하는지 확인한다.
5. Draft PR을 Ready for review로 전환한다.
6. PR 전체를 `geon`에 병합한다.

연구·검토 이력을 남기려면 merge commit 방식이 적합하다. 저장소 정책이 squash를 요구하면 squash도 가능하다.

### 병합 시 충돌 가능성이 높은 공유 파일

- `.github/workflows/ci.yml`
- `README.md`
- `docs/README.md`
- `PACKAGE_MANIFEST.md`
- `go/service-control-api/README.md`
- `go/service-control-api/internal/webui/webui.go`
- `go/service-control-api/internal/webui/webui_test.go`

`internal/llmop`, `internal/llmopbridge`, `cmd/llmop-demo`, `docs/llm-op`, `examples/llm-op`, `schemas/llm-op`은 대부분 격리된 신규 영역이므로 상대적으로 충돌 위험이 낮다.

## 7. 검증 상태

- 이전 LLM_Op 커밋은 GitHub Actions에서 두 Go 모듈의 `go test ./...`, `go vet ./...`, Go CLI와 team validation 성공
- 최신 geon 통합·Guard-first 변경은 새 원격 CI로 재검증 필요
- dependency-free 브라우저 Node 계약 테스트 11건 성공
- geon Playwright 테스트는 저장소에 선언된 package/lockfile이 없어 현재 CI 범위 밖
- 작업 트리 기준 demo fixture 및 문서 교차 참조 확인

문서에 남아 있는 "로컬에서 Go를 실행하지 않았다"는 표현은 이 Windows PC의 보안 정책 때문에 로컬 Go binary를 실행하지 않았다는 의미다. 최신 커밋의 Go test와 vet 결과는 푸시 뒤 원격 GitHub Actions를 기준으로 확인한다.

## 8. 구현 완료 범위와 의도적으로 제외한 범위

### 구현 완료

- 자연어 요청 및 상태·로그 입력 계약
- deterministic Request Guard와 정규화
- LLM ① Safeguard prompt, Schema, parser와 제어 흐름
- LLM ② Proposal prompt, Schema, parser와 제어 흐름
- 의미·자원·freshness·readiness 검증
- geon Common JSON bridge
- AppDeploy `DeploymentCreateRequest` 초안 생성
- 47개 계약 scenario와 8개 웹 시연 흐름
- 오프라인 CLI와 서버 없는 수동 시연 페이지
- 테스트, CI 및 동료 인계 문서

### 현재 브랜치의 의도적 범위 밖

- 실제 Qwen API 호출과 모델 선택/ranking
- 실제 서버 상태·로그 수집 Adapter 구현
- 인증된 운영용 LLM HTTP endpoint
- target, runtime, cloud provider, credential 선택
- AppDeploy POST와 실제 배포
- 승인, idempotency, 실제 배포 검증. geon의 Operation Optimization/feedback loop는 최신 통합 기준에는 존재하지만 LLM_Op 소유가 아님
- SLO, cost, multi-replica, GPU device-memory 요구의 배포 계약 확장

따라서 이 브랜치는 **안전한 LLM 계획 생성 계층과 시연 가능한 계약 구현**까지 완료한 상태다. 운영 배포가 완료된 것은 아니다.

## 9. 병합 전 동료에게 요청할 최종 확인

동료 또는 GPT에게 다음 질문을 중심으로 검토를 요청하면 된다.

1. geon의 어느 trusted in-process orchestrator가 `ReviewWithConfig → ProjectApprovedInitialFlow → PrepareApprovedWithConfig`를 연결할 것인가? 외부 resume HTTP API는 만들지 않는가?
2. Operation Context Adapter 구현자는 `08-operation-context-adapter-contract.md`를 그대로 제공할 수 있는가?
3. AppDeploy 담당자는 `prepared_request`와 `not_submitted` 인계 형식을 수용할 수 있는가?
4. geon의 최신 변경과 공유 web UI/CI 파일에 충돌이 생기지 않았는가?
5. 운영 연결 시에도 live model 호출, target 선택 및 AppDeploy 제출 권한이 명시적 승인 없이 열리지 않는가?

이 다섯 가지가 합의되면, `LLM_Op`의 핵심 코드를 다시 작성할 필요 없이 작은 integration PR로 다음 단계를 진행할 수 있다.
