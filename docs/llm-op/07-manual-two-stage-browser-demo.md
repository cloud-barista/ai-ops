# 수동 2단계 LLM 브라우저 시연

## 목적

이 시연은 자연어 요청과 합성 서버 상태가 다음 순서로 연결되는 모습을 연구자에게 보여준다.

~~~text
사용자 요청·상태
  -> 결정적 Request Guard
  -> LLM 1: 자연어 Safeguard
  -> LLM 2: bounded Manifest Proposal
  -> 결정적 parser·semantic·readiness Guard
  -> AppDeploy DeploymentCreateRequest 초안
~~~

브라우저 페이지 주소는 서비스 실행 기준 "/llm-op-demo"다. 기존 geon 3-view 화면과 파일·route를 분리하여 양쪽 작업자가 같은 index와 app.js를 동시에 수정하지 않도록 했다.

페이지 자체는 LLM API, model endpoint, AppDeploy endpoint를 호출하지 않는다. 시연자는 prompt를 외부 AI 서비스에 직접 옮길 수 있지만, 그 외부 서비스의 호출·과금·보존은 페이지가 통제하거나 관측하지 못한다.

## 시연 모드

| 모드 | 페이지의 모델 API 호출 | 시연자의 외부 AI 입력 | AppDeploy POST | 용도 |
| --- | ---: | ---: | ---: | --- |
| 저장 예시 재생 | 0 | 0 | 0 | 네트워크 없이 발표 흐름 재현 |
| 수동 AI 중계 | 0 | Safeguard 1회, allow일 때 Proposal 1회 | 0 | 임의 AI 서비스의 contract 적합성 관찰 |

"수동 AI 중계"를 전체 무호출 모드라고 부르면 안 된다. 페이지가 호출하지 않을 뿐, 사용자가 선택한 외부 AI 서비스는 네트워크를 사용하고 비용을 청구할 수 있다.

## 두 LLM의 의미

LLM 1과 LLM 2는 반드시 서로 다른 모델이라는 뜻이 아니다. 현재 설계는 caller가 고정한 같은 candidate "qwen3.5-ops-planner"에 두 번의 별도 completion을 요청한다.

- LLM 1은 allow_request, request_clarification, reject_request 중 하나를 제안한다.
- allow_request가 strict 검증을 통과할 때만 LLM 2 입력을 연다.
- LLM 2는 전체 Manifest나 명령어가 아니라 action, reason, confidence, accelerator, CPU·memory·GPU·storage를 제안한다.
- LLM 1의 출력이나 설명은 LLM 2 prompt에 넣지 않는다. 두 단계는 같은 bounded input과 서로 다른 required_output을 사용한다.
- 결정적 Go Guard만 승인 경계다. 두 LLM은 모두 비신뢰 제안자다.

## 사전 조건

1. service-control-api의 embedded web UI를 평소 연구 환경 방식으로 실행한다.
2. 브라우저에서 "/llm-op-demo"를 연다.
3. 저장 예시가 아니라 외부 AI를 쓸 경우 새 대화를 권장한다.
4. 가능하면 AI 서비스에서 system과 user role을 별도로 입력한다.
5. tool, plugin, browsing, code execution 기능은 끈다. 이 시연은 JSON completion만 필요하다.
6. 저장소에 포함된 합성 데이터만 사용한다. 실제 token, API key, kubeconfig, 내부 ID, 운영 로그를 복사하지 않는다.

일반 채팅창에 "[SYSTEM]"과 "[USER]"가 합쳐진 prompt를 한 번에 붙여넣는 방식은 역할 우선순위를 보장하지 않는다. 이 경우 결과는 API 동등 검증이 아니라 contract simulation이다.

## 빠른 시연 절차

### 1. 시나리오 선택

왼쪽에서 대표 시나리오를 고른다. 요청, workload 서비스, readiness, 정규화된 operation context, 학습 포인트를 먼저 읽는다.

"이 시나리오 시작"을 누르면 브라우저가 저장된 결정적 사전 검사 결과를 적용한다.

- passed이면 Safeguard 입력이 열린다.
- rejected이면 두 LLM 영역을 열지 않고 REQUEST_REJECTED로 끝난다.

브라우저의 사전 검사 결과는 materialized fixture다. 임의로 편집한 자연어를 authoritative하게 검사하는 범용 JavaScript Request Guard가 아니다.

### 2. LLM 1: Safeguard

SYSTEM과 USER를 별도로 복사하거나 "전체 복사"를 사용한다. 외부 AI 응답은 설명이나 Markdown code fence를 제거하여 사람이 고치는 것이 아니라, JSON object만 반환하도록 다시 요청한다.

응답 원문을 Safeguard raw JSON 영역에 붙이고 "응답 검증 후 다음 단계"를 누른다.

| 결과 | 다음 상태 |
| --- | --- |
| 유효한 allow_request | Proposal 입력을 연다 |
| request_clarification | CLARIFICATION_REQUIRED로 종료 |
| reject_request | REQUEST_REJECTED로 종료 |
| parser·계약 실패 | MODEL_UNAVAILABLE로 표시하고 Proposal은 잠근다 |

발표만 재현할 때는 "예시 응답 넣기"를 누른다. 이 버튼은 저장된 JSON 문자열을 입력 영역에 넣을 뿐 모델을 호출하지 않는다.

### 3. LLM 2: Manifest Proposal

Safeguard allow가 검증된 뒤에만 같은 방식으로 두 번째 prompt를 외부 AI에 입력한다. raw JSON을 Proposal 영역에 붙이고 "두 번째 출력 검증"을 누른다.

페이지는 다음을 검사한다.

- 최상위 JSON object 하나
- trailing text, 중복 key, 32단계 초과 중첩, 64 KiB 초과
- canonical lowercase exact key와 enum
- reason code, reason, confidence, assumptions 상한
- CPU 1..256, GPU 0..16, memory 최대 2Ti, storage 최대 64Ti
- GPU count와 none/nvidia accelerator 일치
- 선택한 시나리오의 사용자 exact 자원값과 완전 일치
- reason과 assumptions의 대표적인 비밀·식별자·명령 표현

중복 key 예시는 raw 문자열에서 검사한다. JSON.parse 뒤 다시 저장한 값은 중복 정보가 사라지므로 증적으로 사용하지 않는다.

### 4. 최종 결과 읽기

성공 화면에는 다음이 함께 나온다.

- second_llm_output: 두 번째 LLM의 검증된 bounded proposal
- prepared_request: AppDeploy가 받을 DeploymentCreateRequest 모양의 초안
- readiness: fresh_ready 또는 unknown
- submission_mode: not_submitted
- appdeploy_calls: 0
- evidence_model: manual-output-not-provider-attested
- 각 prompt의 SHA-256: 브라우저 Web Crypto를 사용할 수 있을 때만 계산

HANDOFF_READY는 request body 초안이 준비됐다는 뜻이다. 다음을 뜻하지 않는다.

- 실제 Qwen 또는 다른 모델의 identity가 증명됨
- 서버 capacity와 배치 가능성이 증명됨
- AppDeploy 권한이 승인됨
- 명령어가 생성되거나 실행됨
- 실제 서비스 배포가 성공함

readiness가 unknown인 성공은 상태 snapshot이 없어서 모순을 발견하지 못했다는 뜻이다. "서버 준비 완료"로 읽으면 안 된다.

## 실행 가능한 대표 시나리오

| ID | AI 서비스 | 핵심 경로 | 기대 상태 |
| --- | --- | --- | --- |
| direct-fresh-gpu-success | 한국어 질의응답 | fresh 지연 경보 + exact GPU | HANDOFF_READY |
| direct-cpu-explicit-without-snapshot | 의도 분류 | 상태 없음 + exact CPU | HANDOFF_READY, readiness unknown |
| browser-embedding-cpu-success | 문서 임베딩 | exact CPU 성공 확장 | HANDOFF_READY, readiness unknown |
| browser-vlm-gpu-success | 문서 VLM | exact GPU 성공 확장 | HANDOFF_READY, readiness unknown |
| ambiguous-resources-clarification | 문서 임베딩 | "적당한 사양" | CLARIFICATION_REQUIRED |
| target-selection-request | 문서 VLM | VM/Target 직접 선택 | Request Guard에서 REQUEST_REJECTED |
| prompt-injection-in-log | 질의응답 | 로그 속 prompt-control | Request Guard에서 REQUEST_REJECTED |
| duplicate-proposal-json-key | 질의응답 | 두 번째 출력의 중복 action | MANIFEST_REJECTED |

47개 정적 catalog 중 6개를 실제 context와 함께 연결했고, 기존에 성공 golden이 없던 embedding과 VLM은 브라우저용 materialized success 2개로 보완했다. 이 두 확장은 47개 catalog 수를 바꾸지 않는다.

## 상태 전이와 호출 원장

~~~text
INPUT_SELECTED
  -> REQUEST_PRECHECK_REJECTED
  또는
  -> SAFEGUARD_INPUT_READY
     -> SAFEGUARD_INVALID
     -> SAFEGUARD_REJECTED
     -> CLARIFICATION_REQUIRED
     -> SAFEGUARD_ALLOWED
        -> PROPOSAL_INPUT_READY
           -> PROPOSAL_INVALID
           -> CLARIFICATION_REQUIRED
           -> REQUEST_REJECTED
           -> HANDOFF_READY_NOT_SUBMITTED
~~~

불변식:

- captured raw output과 validated output은 다르다.
- SAFEGUARD_ALLOWED 전 Proposal을 열지 않는다.
- clarification과 reject에는 prepared_request가 없다.
- 실패 상태에는 endpoint, command, Target selection이 없다.
- HANDOFF_READY_NOT_SUBMITTED도 AppDeploy call은 0이다.
- 사람이 JSON을 고쳐 다음 상태로 승격시키지 않는다.

## 사람과 동료 AI의 역할

| 주체 | 허용 역할 | 금지 역할 |
| --- | --- | --- |
| 시연자 | 합성 입력 복사, raw 출력 보존, 검증 상태 설명 | 누락값·key·자원값을 고쳐 성공으로 만들기 |
| 외부 AI | 두 bounded JSON 제안 | 권한·Target·provider·runtime·명령 결정 |
| 동료 Codex/AI | 문서·schema·fixture 비교, 오류 원인 설명 | raw 출력 대신 새 JSON을 만들어 성공 증적으로 대체 |
| LLM_Op Guard | parser, semantic, readiness, Manifest 검증 | 실제 배포·AppDeploy POST |
| AppDeployer 담당 | Manifest 재검증, target-specific 변환 | 모델 reason만 신뢰하여 실행 |

## 구현 위치

- 전용 page: go/service-control-api/internal/webui/static/llm_op_demo.html
- 전용 style: go/service-control-api/internal/webui/static/llm_op_demo.css
- 화면 state: go/service-control-api/internal/webui/static/llm_op_demo.js
- prompt·scenario·validator: go/service-control-api/internal/webui/static/llm_op_demo_contract.js
- 순수 Node contract test: go/service-control-api/internal/webui/llm_op_demo_test.js
- embedded route: go/service-control-api/internal/webui/webui.go

브라우저 prompt 상수는 Go의 실제 prompt 상수와 byte-identical한지 Node test가 확인한다. prompt를 바꿀 때 양쪽 중 하나만 수정하면 test가 실패한다.

## 알려진 한계

- 브라우저는 curated fixture의 사전 검사 결과를 재생한다. 임의 사용자 문장의 전체 Go Request Guard를 복제하지 않는다.
- 브라우저 validator는 발표용 mirror다. authoritative Go parser의 모든 node count, Unicode hygiene, normalization, readiness 규칙을 대체하지 않는다.
- 외부 AI provider의 model identity, finish reason, tool call, retention, cost를 검증하지 않는다.
- 일반 채팅 UI가 system role을 보장하지 않을 수 있다.
- 결과를 서버에 저장하거나 AppDeploy에 보내지 않는다. 새로고침하면 세션 증적이 사라진다.

따라서 연구 결과로 보존할 때는 raw 두 출력과 prompt digest를 별도 기록하고, 같은 raw 출력을 offline Go pipeline에 다시 넣어 authoritative 결과를 생성해야 한다.
