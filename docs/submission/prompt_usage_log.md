# 프롬프트 사용 및 공유 기록

## 1. 목적

이 문서는 1차년도 Go 기반 service-control prototype의 대표 prompt category와 prompt-sharing rule을 기록합니다. private conversation, credential, token, personal data를 노출하지 않으면서 개발 투명성을 지원하기 위한 문서입니다.

## 2. Prompt 관리 정책

Prompt 기록은 다음 규칙을 따릅니다.

- 정리된 representative prompt template만 저장합니다.
- private conversation transcript를 저장하지 않습니다.
- cloud credential, API key, token, personal data, private account information을 포함하지 않습니다.
- prompt는 prototype boundary와 일치해야 합니다.
- 검증 증거 없이 final benchmark, production readiness, actual GPU VM provisioning 같은 unsupported claim을 만들도록 요청하지 않습니다.

## 3. 주요 Prompt 범주

| 범주 | 목적 |
| --- | --- |
| README organization prompt | 저장소를 공식 산출물 관리 문서로 구성 |
| Requirements definition prompt | 프로젝트 범위, 기능 요구사항, 비기능 요구사항, 검증 방법 정의 |
| Design deliverable writing prompt | 공식 설계 산출물 Markdown 원본 작성 |
| Go test and validation prompt | Go test와 team-validation evidence 실행/요약 |
| Error fixing and log analysis prompt | command failure 분석과 error message 보존 |
| DOCX generation prompt | 도구가 있을 때 Markdown 산출물을 DOCX 제출본으로 변환 |

## 4. 예시 Prompt Template

### README 구성 Prompt

```text
Revise README.md as an official deliverable management document for a
1st-year Go-based functional prototype. Separate submission artifacts, design
deliverables, and validation evidence. Do not claim production readiness or
standardized LLM evaluation completion.
```

### 요구사항 정의서 Prompt

```text
Revise docs/submission/requirements_definition.md with sections for document
overview, project scope, functional requirements, non-functional requirements,
submission artifact requirements, development guide requirements, validation
method, prototype boundary, and related artifacts.
```

### 설계 산출물 작성 Prompt

```text
Create official design deliverable Markdown files for LLM operation management,
agent registration management, and AI application deployment/control inference
optimization. Keep docs/design as supporting documents and do not delete them.
```

### Go Test 및 검증 Prompt

```text
Run go test ./... in go/aiops-guard and go/service-control-api, then run
team-validation. Record only the actual command outputs and expected prototype
signals.
```

### 오류 수정 및 로그 분석 Prompt

```text
Analyze the failed command output, identify whether the issue is environment,
configuration, code, or external infrastructure, and preserve the exact error
message in the validation log.
```

### DOCX 생성 Prompt

```text
Generate DOCX submission copies from Markdown sources using pandoc if
available. If conversion fails, do not claim DOCX generation. Record the source
file and target file mapping.
```

## 5. 공유 Prompt 사용 방식

공유 prompt는 전체 private conversation log가 아니라 template 형태로 저장합니다. 프로젝트 구성원에게 공유할 때 각 prompt에는 다음을 포함합니다.

- 목적
- 입력 파일
- 기대 출력 파일
- boundary statement
- sensitive-data exclusion rule
- human review requirement

## 6. 사람 검토

사람 검토자는 생성 text가 다음을 만족하는지 확인해야 합니다.

- 담당 연구 범위와 일치
- prototype-level wording 사용
- final benchmark claim 회피
- production-ready claim 회피
- unverified cloud provisioning claim 회피
- 기존 repository path로 link
- private 또는 sensitive information 노출 없음

## 7. 경계

이 기록은 문서화 보조 자료입니다. model performance, coding agent performance, production readiness를 증명하지 않습니다. 이 저장소에서 prompt를 어떻게 공유하고 검토해야 하는지 기록합니다.
