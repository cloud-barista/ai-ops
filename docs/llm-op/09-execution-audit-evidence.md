# Trusted Automation 실행 감사 증적 계약

## 목적과 적용 범위

이 문서는 `POST /api/v1/agent-control/trusted-automation-runs`로 시작하는 Guard-first 실행의 분석용 증적을 로컬 파일로 보존하는 규범 계약이다. 증적은 최초 요청 binding, LLM_Op Safeguard, geon의 분석·추천·Agent·Guard 결과와 최종 종료 상태를 같은 실행 identity로 연결한다.

감사 증적은 정책 결정의 입력이나 승인 수단이 아니다. 감사 파일이 존재하거나 hash chain이 유효하다는 사실만으로 요청자, producer, 모델, 결과의 진정성 또는 배포 권한을 증명하지 않는다. HTTP access log, 운영 Metric, 외부 SIEM, AppDeploy 자체 감사 로그도 이 계약의 대체물이 아니다.

현재 경계는 다음과 같다.

- trusted automation API가 만든 한 번의 실행 attempt만 기록한다.
- 실제 AppDeploy 제출, VM 명령 실행, 과금 또는 외부 모델 호출을 새로 활성화하지 않는다.
- bind 이전의 malformed raw HTTP body는 저장하지 않는다.
- 기존 in-memory `AutomationRun` 조회와 별개로 재시작 뒤에도 분석할 수 있는 파일 증적을 남긴다.

## 저장 레이아웃

기본 audit root는 저장소 기준 `runs/trusted-automation`이다. 실행별 산출물은 UTC 시작일을 기준으로 다음과 같이 분리한다.

```text
runs/trusted-automation/
└── YYYY-MM-DD/
    └── audit-<server-id>/
        ├── events.jsonl
        └── summary.json
```

- `<server-id>`는 서버가 생성한 bounded opaque ID다. 사용자 입력, `requested_by`, AppVersion ID, artifact 이름을 경로에 사용하지 않는다.
- `events.jsonl`은 stage 순서대로 기록한 append-only JSON object 모음이다. 각 행은 독립된 UTF-8 JSON object이고 줄바꿈 한 개로 끝난다.
- `summary.json`은 한 실행의 terminal 상태를 사람이 바로 검토할 수 있게 정리한 canonical 요약이다.
- 임시 파일은 같은 실행 디렉터리에 만들고 flush 뒤 atomic rename으로 `summary.json`을 교체한다.
- audit root 밖으로 벗어나는 상대 경로, `..`, 경로 구분자 또는 caller-controlled filename을 허용하지 않는다.

런타임 산출물 전체는 이미 Git에서 제외된 `runs/` 아래에 둔다. 제출용 대표 증적이 필요하면 비밀정보와 로컬 절대경로가 없음을 다시 검토한 뒤 별도 evidence package로 복사한다.

## 구성과 장애 정책

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `AIOPS_EXECUTION_AUDIT_DIR` | `<repo>/runs/trusted-automation` | audit root. 상대 경로는 repo root 기준으로 해석한다. |
| `AIOPS_EXECUTION_AUDIT_REQUIRED` | `true` | 감사 저장을 continuation의 필수 조건으로 둘지 여부 |

`AIOPS_EXECUTION_AUDIT_REQUIRED=true`가 기본 strict 모드다.

- audit root 초기화 또는 최초 event 기록에 실패하면 geon continuation을 시작하지 않는다.
- Safeguard 거부·명확화 결과는 승인으로 바꾸지 않고 원래 terminal 결과를 유지한다.
- 진행 중 기록 실패는 안전한 고정 오류 코드로 종료하고, 기록 실패를 이유로 외부 동작을 자동 재시도하지 않는다.
- 각 실행의 최초 event 전에 구성 경로와 쓰기 가능성을 검증한다.

`AIOPS_EXECUTION_AUDIT_REQUIRED=false`는 제한된 개발 편의용 degraded 모드다.

- 감사 저장 실패가 Safeguard 또는 Guard 결정을 변경하지는 않는다.
- API 응답의 audit reference는 `persistence_status=DEGRADED`를 반환한다.
- `degraded`는 증적이 불완전하거나 없다는 뜻이지 실행 성공 또는 안전 승인을 뜻하지 않는다.
- 안전한 console event에는 오류 원문 대신 `AUDIT_PERSISTENCE_FAILED` 같은 고정 code만 남긴다.

두 모드 모두 이미 완료된 동작을 감사 파일 실패만으로 재실행하지 않는다. 파일 저장 실패가 rejected 상태를 approved 상태로 바꾸는 fallback도 금지한다.

## 파일 권한

- audit 실행 디렉터리: `0700`
- `events.jsonl`, `summary.json`, 임시 파일: `0600`
- 새 파일을 만들 때 기존 파일을 따라가는 symlink를 허용하지 않는다.
- Windows에서는 POSIX mode bit만으로 ACL을 보장할 수 없다. 서비스 계정 전용 audit root와 상속 ACL을 별도 운영 경계로 확인해야 한다.

## 비저장 정보와 허용 정보

감사 파일은 기존 domain object를 그대로 `json.Marshal`해서 만들지 않는다. 별도의 typed allowlist DTO에 안전한 필드만 복사한다.

다음 값은 redaction 후에도 저장하지 않는다.

- 사용자 요청 원문, preview 또는 일부 인용
- caller-controlled `requested_by`
- AppVersion ID, deployment/target hint 원문
- artifact URI, entrypoint, archive/file 이름
- labels, arbitrary parameters, 자유 형식 metadata
- HTTP header, cookie, authorization, query string
- API key, token, secret 환경변수 이름과 값
- LLM prompt, completion, reason, assumptions 원문
- provider endpoint와 provider error 원문
- upstream `err.Error()` 또는 stack trace

필요한 결합 증거는 SHA-256 digest와 typed allowlist로 대신한다.

- digest 문자열은 algorithm이 별도 필드로 고정된 곳에서는 64자리 lowercase hex 형식이다.
- request digest는 bounded canonical representation에서 계산한다.
- `requested_by`와 AppVersion ID가 결합 확인에 필요하면 원문이 아니라 namespace가 고정된 SHA-256만 기록한다.
- policy는 공개 version만 기록하고 prompt/config 원문은 기록하지 않는다.
- provider와 actual model은 검증된 bounded display label만 허용하며 endpoint, response body와 오류 원문을 결합하지 않는다.
- 자원 결과는 CPU, memory MiB, storage GiB, accelerator type/count, replica처럼 의미와 상한이 정해진 값만 기록한다.
- 상태, action, stage, Guard 결과, 오류는 고정 enum 또는 bounded reason code만 기록한다.
- 사용자 입력에서 온 식별자가 꼭 필요하면 원문 대신 별도 규약의 pseudonymous digest를 사용하고 digest 대상 namespace를 함께 기록한다.

단순 regex redaction은 opaque token이나 PII를 완전하게 찾지 못하므로 원문을 먼저 저장한 뒤 가리는 방식은 허용하지 않는다.

## `events.jsonl` event 계약

각 행은 schema의 `$defs.audit_event`를 따른다. 핵심 필드는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `schema_version` | 항상 `ai-ops.trusted-automation-audit/v1` |
| `audit_id`, `sequence` | 실행 identity와 1부터 증가하는 순서 |
| `recorded_at` | 서버 UTC RFC 3339 timestamp |
| `stage`, `action`, `outcome` | typed stage, 수행한 동작과 결과 |
| `identity` | server-generated request/correlation/trace/run/message ID의 허용 subset |
| `evidence` | digest, 정책 version, typed decision·Guard·자원·오류 evidence |
| `integrity.previous_event_sha256` | 첫 event에서는 생략하고, 이후에는 직전 `payload_sha256` |
| `integrity.payload_sha256` | 현재 event의 canonical hash-chain digest |

권장 stage 순서는 다음과 같다. 실행하지 않은 stage를 성공처럼 채우지 않는다.

```text
request
safeguard
geon_revision_flow
approved_projection  # full projection을 사용하는 경로에서만
terminal_response
```

하나의 stage는 `action`으로 시작·검증·완료를 구분할 수 있다. 예를 들어 Safeguard는 `review_started`, `review_completed`, `approval_binding_validated`를 사용한다. Safeguard가 요청을 멈추면 `safeguard` 다음에 바로 `terminal_response`를 기록한다. geon 오류가 발생하면 실행하지 않은 후속 stage를 만들지 않고 terminal failure code로 끝낸다.

## `summary.json` 계약

`summary.json`은 `schemas/llm-op/trusted-automation-audit.schema.json`의 top-level schema를 따른다. 아래 영역을 정의하며 `failure`는 실패일 때만 존재한다.

### `identity`

서버가 생성하거나 검증한 `audit_id`, request/correlation/trace ID와 선택적 automation run/message ID를 연결한다. AppVersion ID와 `requested_by`는 포함하지 않는다.

### `request_summary`

원문 대신 `payload_sha256`, `input_type`, `candidate_id_sha256`, 요청 길이와 caller/AppVersion digest만 포함한다. 구조화 요청도 artifact, label, arbitrary metadata는 버린다.

### `timeline`

`events.jsonl`의 event를 같은 순서로 투영한다. `timeline`은 별개의 결정 기록이 아니며 events 원문과 sequence·identity·hash가 일치해야 한다.

### `result_summary`

최종 trusted orchestration status, Safeguard action/reason code, geon terminal status, Guard 결과, revision·manifest digest와 mock/submission mode만 포함한다. typed resource 결과는 해당 geon timeline event에만 존재한다. 모델 또는 Agent의 자유 형식 설명은 포함하지 않는다.

### `failure`

성공이면 생략된다. 실패이면 stage와 고정 error code만 기록한다. provider·filesystem·upstream 오류 원문은 저장하지 않는다.

### `integrity`

마지막 event hash, summary payload hash, algorithm과 `authenticated=false`를 기록한다. event 수는 `timeline` 길이와 `events.jsonl` 행 수로 교차 확인한다.

SHA-256 hash chain은 event 삭제·순서 변경·우발적 수정을 탐지하기 위한 것이다. 파일을 수정할 수 있는 주체는 전체 chain을 다시 계산할 수 있으므로 작성자 인증, 부인 방지 또는 공격자에 대한 진정성을 제공하지 않는다. 그런 보장이 필요하면 별도 보안 경계의 signing/HMAC key와 외부 anchor를 추가해야 한다.

### `artifacts`

같은 audit root를 기준으로 한 `events_path`, `summary_path`만 기록한다. 두 값은 `YYYY-MM-DD/audit-<server-id>/...` 형태의 상대경로이며 절대경로, audit root, host/user 이름은 노출하지 않는다. 파일 digest는 각각 event/summary의 `integrity`와 API 외부 reference에서 검증하며, `summary.json`은 자기 자신 안에 최종 파일 digest를 넣지 않는다.

## API 노출 경계

API 응답은 파일 내용이나 절대 파일 시스템 경로가 아니라 다음과 같은 bounded reference만 노출한다.

```json
{
  "audit": {
    "audit_id": "audit-0123456789abcdef",
    "schema_version": "ai-ops.trusted-automation-audit/v1",
    "persistence_status": "COMPLETE",
    "complete": true,
    "event_count": 7,
    "relative_directory": "2026-08-26/audit-0123456789abcdef01234567",
    "events_path": "2026-08-26/audit-0123456789abcdef01234567/events.jsonl",
    "summary_path": "2026-08-26/audit-0123456789abcdef01234567/summary.json",
    "last_event_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "summary_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
  }
}
```

`persistence_status`는 `RECORDING`, `COMPLETE`, `DEGRADED` 중 하나다. `DEGRADED`는 파일 증적이 완전하지 않다는 명시적 경고다. audit root 기준 상대경로와 SHA-256은 증적 참조로 허용하지만 audit root 자체, drive 문자, host/user 이름을 포함한 절대경로는 노출하지 않는다. Reference의 경로는 서버 운영자가 로컬에서 확인하기 위한 값이지 HTTP download URL이 아니다. `audit_id`를 안다고 파일을 내려받을 수 있는 HTTP route는 제공하지 않는다. 감사 파일 조회·검색·다운로드 API는 인증·인가·retention·redaction 계약이 별도로 합의되기 전까지 추가하지 않는다.

## 분석 및 검증 기준

사후 분석은 다음 순서로 수행한다.

1. `summary.json`이 schema에 맞는지 확인한다.
2. `events.jsonl`의 각 행이 `$defs.audit_event`에 맞고 sequence가 1부터 연속인지 확인한다.
3. 첫 event에서 `previous_event_sha256`가 생략되고 이후 event의 link가 직전 `payload_sha256`와 같은지 확인한다. hash 재계산 시 현재 payload의 `integrity.payload_sha256`를 빈 값으로 둔 canonical JSON을 사용한다.
4. summary의 identity, timeline, terminal status와 마지막 event hash가 events와 일치하는지 확인한다. summary payload hash도 `integrity.payload_sha256`를 빈 값으로 둔 canonical JSON에서 재계산한다.
5. 금지된 사용자 원문, token, header, artifact URI, label, prompt, completion, provider 오류가 어떤 파일에도 없는지 검사한다.
6. `degraded` 응답은 complete evidence로 집계하지 않는다.

필수 회귀 범위는 approved, Safeguard reject, clarification, model unavailable, geon reject/error, idempotent replay, 동시 실행, process interruption, permission denied와 audit disk failure다. strict 모드는 audit 시작 실패 뒤 geon 호출이 0회임을, degraded 모드는 증적 결손을 응답에서 숨기지 않음을 검증한다.
