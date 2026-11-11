# geon 공유용 버그 및 계약 불일치

## 목적

LLM_Op 정적 검토 중 발견한 geon/AppDeployer 경계의 문제를 공유한다. 이 문서는 geon 코드를 수정하지 않으며, geon 작업자가 재현·수정 여부를 결정할 수 있도록 근거와 예상 영향을 기록한다.

초기 항목은 2026-08-05 기준이고, 항목 8은 `geon@1999d79`를 2026-08-12에 정적으로 검토한 결과다. Go 테스트나 AppDeployer 종단간 실행으로 재현한 결과는 아니므로, geon 쪽 실행 환경에서 재현 여부를 확인해야 한다.

## 확인된 버그

### 1. AppDeployer COMPLETED 성공 상태 미처리

- AppDeployer OpenAPI의 DeploymentStatus는 RUNNING과 COMPLETED를 모두 정상 상태로 정의한다.
- geon internal/deploymentplanner/planner.go의 classifyTerminalStatus는 RUNNING만 성공 terminal로 처리한다.
- 단발성 작업이 COMPLETED를 반환하면 terminal로 끝나지 않고 polling을 계속하다 POLL_TIMEOUT이 될 수 있다.
- finish 함수도 성공 상태를 항상 RUNNING으로 덮어써 원래 COMPLETED 상태를 잃는다.

권장 검토:

1. COMPLETED도 성공 terminal로 분류한다.
2. finish에서 result.Status를 RUNNING으로 고정하지 않고 AppDeployer가 반환한 terminal 상태를 보존한다.
3. RUNNING 서비스형 앱과 COMPLETED 배치형 앱의 test case를 분리한다.

### 2. 한글 요청 길이의 byte/rune 기준 불일치

- internal/plannerguard/guard.go는 len([]rune(requestText))로 8000자 제한을 검사한다.
- internal/deploymentplanner/generator.go는 len(input.NaturalLanguageRequest)로 byte 수를 검사한다.
- 긴 한글 요청은 Request Guard를 통과한 뒤 Generator에서 더 이른 시점에 거부될 수 있다.

권장 검토:

- Generator도 utf8.RuneCountInString 또는 []rune 기준을 사용해 Guard와 동일한 문자 수 계약을 유지한다.

## 계약 불일치와 통합 위험

### 3. DeploymentManifest JSON Schema와 실제 모델의 drift

- AppDeployer OpenAPI와 양쪽 Go 모델에는 spec.requirements가 있다.
- AppDeployer 및 geon의 deployment_manifest.schema.json에는 requirements 정의가 없다.
- Schema는 resources의 필수 하위 필드, 단위, additionalProperties 제한도 Go Guard보다 느슨하다.

영향:

- Schema만 통과한 unknown field나 잘못된 자원값이 실제 Go Guard에서는 거부될 수 있다.
- 현재 LLM_Op은 JSON Schema 단독 검증을 완료 조건으로 삼지 않고 internal/appdeploy.ValidateManifest를 canonical 검증기로 사용한다.

### 4. 요청 추적과 중복 방지 계약 미완성

- AppDeployer는 X-Request-ID를 받을 수 있으나 geon appdeploy.Client는 현재 이 헤더를 전달하지 않는다.
- X-Request-ID는 추적용이며 idempotency 계약은 아니다.
- POST 응답 유실 뒤 자동 재시도하면 중복 Deployment 생성 위험이 있다.

권장 검토:

- request_id 전달과 idempotency key를 별도 계약으로 다룬다.
- 명시적 중복 방지 계약 전에는 LLM_Op이 자동 제출·재시도를 구현하지 않는다.

### 5. 승인 검증 계약 부재

- AppDeployer OpenAPI는 현재 security: []이며, API key 또는 사용자 승인 reference 검증 계약이 없다.
- geon의 SubmitControlRunRequest에도 approval_reference 검증 정보가 없다.

LLM_Op 대응:

- 초기 구현은 prepare_only만 허용한다.
- approved_submit과 임의 approval_reference는 Request Guard에서 거부한다.

### 6. 두 Manifest 생성 경로의 정책 drift 위험

- 기존 `internal/deploymentplanner.Generator`는 Qwen에 전체 DeploymentManifest를 요청한 뒤 신뢰 필드를 덮어쓰는 경로다.
- 새 공식 offline `internal/llmop.NewOfflineFixturePlanner(...).Prepare`와 향후 live `PrepareWithConfig`는 첫 Qwen 단계에 allow/clarify/reject review만, allow 뒤 내부 `Planner`의 둘째 단계에 action/reason/resources Proposal만 허용한다. 자연어 정확값·Common JSON Profile minima와 선택 Resource exact 값·fresh readiness 모순을 검사한 뒤 Go mapper가 Manifest를 만든다.
- 두 경로를 동시에 공개 route로 유지하면 같은 자연어 요청이 서로 다른 redaction, freshness, semantic Guard와 상태값을 거칠 수 있다.

LLM_Op 대응:

- 기존 route와 Agent Control 코드는 이 브랜치에서 수정하지 않는다.
- `internal/llmopbridge`는 Common JSON 앞 단계의 Profile minima와 선택된 Resource candidate exact single-node 값을 분리해 LLM_Op에 투영하며 ModelRecommendation이나 기존 DeploymentCreateRequestEnvelope를 변환하지 않는다. geon 기본 CPU 최소값보다 큰 catalog 후보도 축소하지 않고 exact 추천값으로 보존한다.
- Draft PR에서 공식 결합점을 하나 정한 뒤, 중복 경로를 유지할 경우 공통 Guard 계약과 golden scenario를 공유해야 한다.

### 7. 자연어 GPU Profile의 device-memory 요구를 AppDeploy v1에서 표현할 수 없음

- geon `LocalRequirementAnalyzer`는 자연어 GPU 요구에 기본 GPU device-memory minimum을 부여한다.
- Agent Control ApplicationProfile과 Resource candidate에는 이 필드가 있지만 AppDeploy v1 DeploymentManifest의 resources에는 CPU, memory, GPU count, storage만 있다.
- 값을 제거한 채 Manifest를 만들면 AppDeploy가 다른 GPU Target을 선택할 때 device-memory 최소값을 강제할 수 없다.

LLM_Op 대응:

- Common JSON bridge는 Profile 또는 선택 Resource candidate 어느 쪽의 device-memory minimum도 조용히 버리지 않고 fail-closed한다.
- 현재 bridge 성공 fixture는 실제 geon 정책과 맞는 CPU-only Profile을 기준으로 둔다. 별도 경계 fixture와 실제 `LocalRequirementAnalyzer` + checked-in catalog 회귀 test는 device-memory가 있는 GPU Common JSON 경로가 명시적 오류로 끝나도록 고정한다.
- AppDeploy schema 확장 또는 resource recommendation과 최종 Target을 연결하는 신뢰 계약이 합의되기 전에는 기존 geon 자연어 GPU Common JSON 경로의 종단간 성공을 주장하지 않는다.

### 8. LocalRequirementAnalyzer의 `GPU 0` 및 replica 기본값이 Guard-first 연결을 막음

- `LocalRequirementAnalyzer`는 자연어에 `gpu` 문자열이 있으면 수량이 0이어도 accelerator를 `Required=true`로 만든다. 따라서 LLM_Op의 명시적 CPU-only 문법인 `GPU 0`이 geon에서는 required GPU count 0이라는 모순된 Profile이 될 수 있다.
- replica를 명시하지 않으면 `Minimum replica count defaulted to 1` assumption이 남는다.
- Guard-first approved bridge는 hidden default로 AppDeploy 초안을 만들지 않기 위해 missing field, assumption 또는 warning이 있는 Profile을 clarification으로 fail-closed한다.
- 결과적으로 `CPU 2, GPU 0, memory 4Gi, storage 20Gi`처럼 LLM_Op 단독 경로에서 유효한 대표 CPU 요청도 현재 LocalRequirementAnalyzer를 거치면 approved Flow 연결에 성공하지 않을 수 있다.

권장 검토:

1. `GPU 0`을 명시적 CPU-only 요구로 해석해 `Required=false`, count/memory 0으로 만든다.
2. resource별 `explicit/defaulted/inferred`와 replica provenance를 구조화해 downstream이 실제 투영 필드만 판단할 수 있게 한다.
3. 사용자에게 replica를 요구할지, trusted policy default로 허용할지 계약을 명시한다.
4. 최초 LLM_Op Safeguard → 실제 `LocalRequirementAnalyzer` → recommendation/decision/revision → approved bridge 회귀 test를 추가한다.

LLM_Op 대응:

- 이번 기준 구현은 문제를 추측 보정하지 않고 fail-closed하며, 실제 geon analyzer를 통과한 CPU/GPU 종단 성공을 주장하지 않는다.
- 구조화 provenance와 GPU 0 의미가 합의되면 bridge fixture와 47개 scenario catalog를 함께 갱신한다.

## 상호 공유 방식

- geon에서 위 문제를 수정하거나 계약 결정을 내리면 LLM_Op Draft PR에 알려 주기를 요청한다.
- LLM_Op에서 추가 버그를 확인하면 이 파일과 Draft PR 대화에 근거를 갱신한다.
- 충돌을 줄이기 위해 geon의 planner.go, generator.go, OpenAPI/Schema는 LLM_Op에서 직접 고치지 않는다.
