# Documentation Map

이 디렉터리는 AI App Deployer 프레임워크를 공유하기 위한 최소 문서만 전면에 둔다.

운영 방향은 다음과 같다.

1. 프레임워크 프롬프트를 먼저 공유한다.
2. 산출물 문서는 최소화한다.
3. 로그와 에러 메시지를 최대한 남긴다.

## 먼저 볼 문서

| 순서 | 문서 | 목적 |
| --- | --- | --- |
| 1 | `prompts/프레임워크_공유_프롬프트.md` | 개발·검증·문서 정리 공통 프롬프트 |
| 2 | `../agent_md/00_scope_common_contract.md` | 담당 범위, 제외 범위, 상태값, 에러 코드 |
| 3 | `../contracts/openapi/openapi.yaml` | API source of truth |
| 4 | `ops/로그_에러_가이드.md` | 로그·에러 메시지 작성 기준 |
| 5 | `evidence/증적_패키지_가이드.md` | 제출 증적 구성 기준 |
| 6 | `release/1차년도_제출_패키지_체크리스트.md` | 최종 제출 전 점검 |

## 최소 산출물

| 문서 | 역할 |
| --- | --- |
| 구조 설계서 | `AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.md` |
| 프로토타입 개발설계서 | `CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.md` |
| API 계약 | `../contracts/openapi/openapi.yaml`, `api/openapi.html` |
| 실행/시험 | `install/설치_활용_가이드_초안.md`, `test/시험_가이드_초안.md` |
| 증적/릴리스 | `evidence/증적_패키지_가이드.md`, `release/1차년도_제출_패키지_체크리스트.md` |

그 외 문서는 참고용이다. 새 문서를 늘리기보다 위 문서와 로그·에러 증적을 최신 상태로 유지한다.

## 최신 구현 기준

- API prefix는 `/api/v1`이다.
- 공식 API 계약은 `contracts/openapi/openapi.yaml`이다.
- 1차년도 artifact type은 `package`, `git`, `binary`, `script`만 허용한다.
- CPU/GPU VM 배포는 SSH runner와 `file://` script upload 흐름을 지원한다.
- CPU/GPU VM 기반 AI Application 배포 방식은 테스트 완료했다.
- 배포 stop 요청은 원격 VM의 배포 프로세스를 종료하고 `STOPPING` -> `STOPPED` 전이를 기록한다.
- Running deployment는 inference proxy API를 통해 `/health`, `/generate` 같은 앱 내부 HTTP endpoint를 호출할 수 있다.
- 실제 ETRI/Innogrid/Bespin API 호출은 아직 구현하지 않고 mock/fixture와 책임 경계만 유지한다.

## 문서 수정 규칙

- OpenAPI를 바꾸면 `docs/api/openapi.html`을 다시 생성한다.
- API 동작이 바뀌면 OpenAPI, 프롬프트, 로그·에러 가이드, 시험 증적 기준만 우선 확인한다.
- 제출 판단이 바뀌면 `docs/evidence`, `docs/release`를 갱신한다.
- 완료한 작업은 루트의 `log.md`에 간단히 기록한다.
