# LLM/코딩 에이전트 교차 검증 문서

## 1. 목적

이 문서는 1차년도 Go 기반 service-control prototype 개발 과정에서 2종 이상 LLM 또는 coding-agent role을 활용하는 개발 절차 검증 방식을 기록합니다. 이는 process validation record이며 performance benchmark가 아닙니다.

실제 product name이 확인되지 않은 경우 의도적으로 중립 role label을 사용합니다. 특정 LLM vendor name이나 coding-agent product name을 임의로 만들지 않습니다.

## 2. Coding Agent 사용 정책

제출 package에 영향을 주는 변경에는 두 역할 검토 정책을 사용합니다.

- primary coding agent role은 구현과 문서 초안을 작성하거나 수정합니다.
- secondary review agent role은 consistency, boundary statement, README link, test command, deliverable completeness를 확인합니다.
- human reviewer가 최종 수용 여부를 판단합니다.

이 절차는 documentation drift를 줄이고 production readiness, final benchmark completion, actual GPU VM provisioning 같은 unsupported claim을 방지하기 위한 것입니다.

## 3. 사용 LLM/코딩 에이전트

| Role label | 목적 | 명명 경계 |
| --- | --- | --- |
| Agent A / primary coding agent | Go/API 문서, 산출물 매핑, 검증 지침 작성 | 중립 role label |
| Agent B / secondary review agent | 생성 문서, README link, prototype boundary wording, test evidence 교차 확인 | 중립 role label |
| `code-cross-check-agent` | `config/ops_llm_benchmark.json`의 코드/문서 교차 검증용 prototype policy role label | 저장소 role label이며 vendor claim이 아님 |

향후 보고서에서 구체적인 model 또는 tool name을 사용하려면, 해당 이름이 실제 사용 도구와 일치하는지 검토자가 확인해야 합니다.

## 4. 역할 배정

| 작업 항목 | Primary role | Review role | Human review |
| --- | --- | --- | --- |
| README 구성 | Agent A | Agent B | 공식 산출물 tone과 link correctness 확인 |
| 요구사항 정의서 | Agent A | Agent B | scope와 artifact requirement 확인 |
| 설계 산출물 | Agent A | Agent B | 공식 산출물명과 1:1 mapping 확인 |
| 프롬프트 사용 기록 | Agent A | Agent B | private conversation 또는 credential leakage 없음 확인 |
| 테스트 및 검증 로그 | Agent A | Agent B | command, expected output, boundary statement 확인 |
| DOCX 변환 | Agent A | Agent B | availability claim 전 파일 존재 확인 |

## 5. 교차 검증 방법

1. 문서 또는 code-adjacent artifact를 생성/수정합니다.
2. 결과를 요청된 deliverable list와 비교합니다.
3. 필수 link가 repository path로 연결되는지 확인합니다.
4. prototype boundary가 명확히 작성되었는지 확인합니다.
5. final benchmark 또는 production-readiness claim이 추가되지 않았는지 확인합니다.
6. DOCX file은 실제 존재할 때만 available로 설명합니다.
7. 실행 지침에 영향을 줄 수 있는 변경이면 Go test와 team-validation을 실행합니다.
8. 남은 limitation을 기록합니다.

## 6. Prompt 예시

공유 전 prompt는 정리해야 합니다. private credential, token, personal data, full private conversation을 포함하지 않습니다.

예시 prompt category:

- "README를 공식 산출물 관리 문서로 수정한다."
- "1차년도 Go 기반 기능 prototype 요구사항 정의서를 작성한다."
- "공식 산출물명과 1:1로 매핑되는 세 개의 설계 산출물 Markdown을 작성한다."
- "명령 출력과 검증 증거를 요약하되 production readiness를 주장하지 않는다."
- "변환 도구가 있을 때만 DOCX 변환본을 생성한다."

## 7. 사람 검토 checklist

사람 검토자는 다음을 확인해야 합니다.

- README title과 section heading이 공식 문서 tone인지
- 필수 제출 산출물이 나열되어 있는지
- 설계 산출물이 공식 이름과 1:1로 매핑되는지
- Markdown source file이 존재하는지
- DOCX file이 존재할 때만 generated 또는 available로 표시했는지
- OpenAPI YAML이 README와 기능/API 가이드에서 연결되는지
- Go test와 team-validation 결과가 정확히 기록되었는지
- prototype boundary와 benchmark boundary statement가 있는지

## 8. 검증 증거

검증 증거는 다음을 포함할 수 있습니다.

- `go/aiops-guard`의 `go test ./...` 출력
- `go/service-control-api`의 `go test ./...` 출력
- `runs/<output-dir>/` 아래 `team-validation` JSON 출력
- README link check
- DOCX file existence check
- human review note

## 9. 경계

이 문서는 개발 및 검토 절차를 설명합니다. LLM 또는 coding agent의 model quality benchmark를 주장하지 않습니다. 또한 human review, repository test, controlled evaluation protocol을 대체하지 않습니다.
