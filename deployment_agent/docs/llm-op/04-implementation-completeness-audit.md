# LLM_Op 구현 완성도 감사

## 결론

현재 `LLM_Op` 브랜치는 자연어 요청과 선택적 운영 관측을 입력받아 두 단계의 LLM JSON 계약을 거친 뒤, 결정적 Go 검증으로 AppDeploy `DeploymentCreateRequest` 초안을 만드는 prepare-only 구현이다. 핵심 경로는 단순 문서나 빈 함수가 아니라 실제 코드로 연결되어 있다. 별도 /llm-op-demo 정적 route에서는 저장 fixture 또는 사용자가 중계한 두 raw JSON으로 이 흐름을 설명·검증한다. 다만 실제 Qwen 호출, LLM 처리 공개 HTTP API, 승인 검증, AppDeploy POST는 의도적으로 활성화하지 않았다. 이 항목들은 숨은 TODO가 아니라 현재 성공 경계 밖의 명시적 release blocker다.

이 감사에서 literal `TODO`, `FIXME`, 충돌 표식과 임시 성공 반환은 발견하지 않았다. Go 실행은 워크스페이스 보안 규칙에 따라 수행하지 않았으므로 컴파일·테스트 통과를 주장하지 않는다. JSON 구문과 demo catalog 교차참조는 PowerShell 정적 검증으로 확인했다.

## 지시사항 대조

| 요청한 목표 | 현재 구현 | 판정 |
| --- | --- | --- |
| 사용자 자연어 + 상태·로그·모니터링 입력 | `Request`, `OperationContext`, `Normalizer` | 구현됨 |
| 자연어 Safeguard LLM | 별도 review prompt와 allow/clarify/reject JSON parser | 구현됨 |
| Manifest 생성용 LLM | 별도 bounded Proposal prompt와 JSON parser | 구현됨 |
| 결정적 정책·세이프가드 | Request Guard, semantic guard, AppDeploy Manifest Guard | 구현됨 |
| AppDeployer가 받을 key shape | `DeploymentCreateRequest{manifest}`를 `handoff.prepared_request`로 생성 | 구현됨; route identity binding은 미구현 |
| 모델 선택은 현재 범위가 아님 | caller가 준 `candidate_id`를 exact binding하며 ranking 없음 | 구현됨 |
| 실제 API 비용 0 | public offline factory에는 JSON 문자열만 들어가고 HTTP client 경로가 없으며 provider/model evidence가 fixture label로 고정됨 | 구현됨 |
| Qwen 연결 코드만 준비 | `PrepareWithConfig`, 기본 `AllowLiveCompletion=false` | 구현됨, live 미검증 |
| geon Common JSON 결합 | Profile/Recommendation subset을 `PlanningConstraints`로 투영 | 구현됨, 공개 route 미결정 |
| 다수 사용자 예시 | 47건 scenario catalog와 4개 AI 서비스 metadata | 구현됨, scenario 실행 증적 아님 |
| 직관적 수동 시연 | 대표 8개 흐름, prompt 복사, 단계별 raw JSON 검증, 두 번째 출력·handoff 표시 | 구현됨; 브라우저 mirror이며 Go 재검증 필요 |
| 서버 상태 Adapter 형식 | operation context producer 규칙·freshness·identity·bounded projection 문서 | 문서화됨; 실제 수집 Adapter는 범위 밖 |

## 감사 중 발견해 해소한 항목

| 발견 | 보완 |
| --- | --- |
| exported constructor에 임의 completion client를 넣어 live 금지를 우회 가능 | client 주입 생성자를 package-private로 바꾸고 public `NewOfflineFixturePlanner`는 pre-recorded JSON만 받도록 제한 |
| 요청의 map·pointer가 검증 뒤 변경될 TOCTOU | 첫 guard 전에 bounded JSON deep snapshot을 만들고 이후 단계가 snapshot만 사용 |
| untyped `application.parameters` denylist 우회 가능 | v1alpha1에서 non-empty parameters를 전면 거부하고 Manifest mapper에서도 제외 |
| user text만 prompt injection 검사 | Qwen-bound resource/monitoring/log/metric 문자열과 status bucket key도 검사 |
| zero-width·bidi·제어 문자로 검사·표시 우회 | input hygiene와 output display hygiene를 분리해 결정적으로 거부 |
| Go JSON decoder의 duplicate-key last-wins | review와 Proposal의 모든 중첩 object에서 duplicate key와 비정규 대문자 key를 선검사 |
| Profile minimum만 있으면 모델이 전역 ceiling까지 자원 확대 가능 | exact recommendation이 없을 때 `max(Profile 최소값, 명확한 사용자 양수값)` 형상과 exact 일치 강제 |
| stale resource snapshot이 ‘정보 없음’으로 바뀌어 생성 가능 | snapshot을 제공했으나 stale로 제외되면 create를 `MANIFEST_REJECTED` |
| readiness boolean만 확인 | fresh Target의 `status=available`, `runtime_health=ok`, CPU·memory·storage 및 필요 GPU boolean을 함께 요구 |
| caller가 Normalizer 보안 상한을 완화 가능 | 10분/1분/collection/field limit보다 큰 override를 거부 |
| policy가 8,000보다 큰 request limit을 설정 가능 | 코드 고정 8,000 rune과 policy limit 중 작은 값을 적용 |
| guard 전에 거대 raw observation을 JSON snapshot | raw 필드 32 KiB, text envelope 2 MiB, collection·parameter budget을 marshal 전에 검사 |
| 자원 부정·대조 표현을 exact 값으로 오인 가능 | resource label과 부정·대조 token이 함께 있으면 지원 grammar 밖으로 거부 |
| invalid candidate label이 실패 evidence에 복사 | bounded display-safe candidate 검증 뒤에만 evidence를 채움 |
| invalid·짧은·거대 request ID가 실패 Result에 그대로 echo | 8..128 byte strong identifier만 Result에 복사하고 나머지는 빈 값으로 처리 |
| offline fixture가 실제 provider/Qwen label을 가장 | `offline-fixture` / `fixture-qwen-contract-not-executed`를 코드 상수로 고정하고 endpoint/API-key field를 거부 |
| 동일 Target ID의 ready/down 중복 행이 순서에 따라 통과 | 모든 fresh resource target ID를 먼저 검증하고 중복이면 `ambiguous_resource_snapshot`으로 거부 |
| monitoring runtime row의 빈 Target ID가 negative evidence를 숨김 | normalization에서 bounded non-empty Target ID를 요구 |
| Target/VM ID 선택, H100/CUDA/CPU SKU, multi-node, OS/port 요구가 조용히 유실 | 알려진 직접 표현을 Request Guard에서 fail-closed하고 회귀 시나리오 추가 |
| model reason/assumptions가 자원값·지원 범위를 허위 설명 | unsupported claim을 거부하고 성공 public explanation은 결정적 Go 문구로 교체 |
| bridge가 unsafe/하위 계약 밖 ID와 거대 user text를 Projection에 복사 | evidence/projected ID 계약을 분리해 bounded 검증하고 user text 8,000 rune/32 KiB·hygiene 적용 |
| live caller가 Normalizer clock을 바꿔 freshness를 우회 | public `PrepareWithConfig`가 내부 system-clock Normalizer를 소유하도록 변경 |

## 현재 성공 경계

`HANDOFF_READY`는 아래를 뜻한다.

1. 요청이 prepare-only이고 결정적 Request Guard를 통과했다.
2. Safeguard review가 bounded JSON으로 allow를 반환했다.
3. Proposal이 bounded JSON과 자원 계약을 통과했다.
4. 제공된 fresh resource snapshot과 Proposal 사이에 결정적 모순이 없다.
5. Go mapper가 non-LLM identity field를 주입했고 AppDeploy Manifest Guard가 승인했다.
6. POST하지 않은 exact request body가 준비됐다.

다음을 뜻하지 않는다.

- 실제 Qwen 모델이 실행됐음
- `actual_model`이 provider attestation으로 증명됐음
- telemetry source가 인증됐음
- 실제 용량·스케줄링·배포 성공이 보장됨
- 사용자 승인이나 AppDeploy POST가 수행됐음

## 의도적으로 미지원하는 계약

다음은 호출자가 우회해도 되는 backlog가 아니다. 지원 계약이 생길 때까지 fail-closed하거나 진입점을 공개하지 않는다.

- non-empty `application.parameters`
- SLO, 비용, multi-replica, GPU device memory의 AppDeploy v1 투영
- Target, VM, Cloud provider, Runtime adapter 선택
- credential, endpoint, command, container/Kubernetes 생성
- 자동 repair/retry/submit loop
- 구조화된 사용자 명확화 왕복 API(`missing_fields`, canonical question, resume token)
- LLM 요청을 처리하는 공개 HTTP API와 인증 principal 결합
- AppDeploy 조회·제출·승인 adapter

## live Qwen 활성화 전 release blocker

`AllowLiveCompletion=true`는 현재 데모 완료 조건이 아니다. 활성화 전 다음을 구현·검토해야 한다.

1. HTTPS 및 host allowlist, redirect 금지, 전용 egress policy
2. 임의 환경변수 이름을 읽지 않는 전용 secret resolver와 API-key allowlist
3. timeout, max output token, rate·비용 budget의 고정 상한
4. provider response의 reported model, `finish_reason=stop`, tool-call 부재 검증
5. configured model과 provider-reported model의 분리 evidence
6. candidate/policy JSON의 duplicate·trailing 값·중복 ID·크기 검증
7. config digest, prompt version/digest, 정책 version 보존
8. 인증 principal이 `requested_by`를 덮어쓰는 경계
9. telemetry와 Common JSON producer의 인증·freshness·replay 방지
10. bridge projection evidence와 최종 Result를 immutable digest로 결합
11. 인증 principal → `requested_by`, registry app identity → `app_version_id`, server-side candidate/deployment/Target binding
12. target hint가 있을 때 trusted snapshot 의무화 또는 readiness-unknown 별도 wire 상태
13. trusted evaluation time과 `evaluated_at` evidence
14. Request/Result/review/Proposal JSON Schema 또는 OpenAPI component와 response size/framing 상한
15. allowlisted structured intent 또는 검증된 문법으로 자연어 의미 유실 경계를 축소
16. opaque/label-free secret과 외부 provider 전송 telemetry에 대한 data-governance gate

## 잠정값 등록부

아래 값은 요구사항에서 증명된 운영 SLO가 아니라 PoC safety envelope다. 코드보다 큰 override는 거부한다.

| 값 | 현재 상한 | 의미 |
| --- | --- | --- |
| user request | 8,000 rune | 자연어 입력 상한 |
| raw field / raw text envelope | 32 KiB / 2 MiB | snapshot 전 메모리·CPU 방어 |
| observation age / future skew | 10분 / 1분 | prompt에 넣을 관측 freshness |
| logs input / retained | 500 / 50 | 정규화 collection 상한 |
| resource targets/runtime health/alarms | 각 100 | 정규화 collection 상한 |
| status buckets | 50 | monitoring map 상한 |
| completion | 64 KiB | 모델 출력 parser 상한 |
| resource ceiling | CPU 256, GPU 16, memory 2 TiB, storage 64 TiB | 전역 비상 상한 |
| model allow/create confidence | 0.5 | 자기보고 confidence의 최소 형식값 |

confidence는 보정된 확률이 아니며 결정적 guard를 완화하지 않는다.

## 검증 증적

이번 감사에서 수행한 검증:

- 저장소 JSON 54개(신규 LLM output schema 2개 포함)의 PowerShell parse
- `validate-llmop-demo-data.ps1`와 동등한 read-only inline 검증으로 4 services, 1 caller-pinned model binding, 47 unique scenarios의 구조·ID·offline invariant 확인
- browser system prompt와 Go prompt 상수의 byte equality, 대표 8개 흐름, strict duplicate JSON, 네 service success, no-fetch UI를 검증한 Node test 10개 통과
- validator script의 PowerShell AST parse 0 error와 변경 문서 local link 존재 확인
- `git diff --check`
- 코드의 `TODO`/`FIXME`/stub 표식, 충돌 표식, 구형 호출 시그니처 정적 scan 및 source/test 대조

로컬 정책상 `.ps1` 파일의 일반 실행은 `PSSecurityException`으로 차단됐다. 실행 정책을 우회하지 않았고, 같은 검사를 PowerShell inline 명령으로 수행했다. 따라서 이 결과는 catalog 구조의 정적 검증이며 Go runtime 시나리오 실행 증적이 아니다.

수행하지 않은 검증:

- `go test`, `go vet`, `gofmt`, binary build
- Qwen endpoint 호출 또는 model weight load
- AppDeploy POST
- scenario 47건의 table-driven runtime 실행

따라서 scenario catalog의 `validation_status=static_contract_catalog_not_executed`를 유지한다.
