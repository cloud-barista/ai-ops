# 04. App Registry 및 Validator 에이전트

## 역할
AI App 등록, 버전 관리, App Spec 검증 기능을 구현한다.

## 구현 대상
- App 등록 API
- App 목록/상세 조회 API
- App Spec JSON Schema 검증
- Go 기반 의미 검증
- 중복 name/version 검증

## 검증 규칙
- `schema_version`은 `appspec.khu.ai/v1alpha1`이어야 한다.
- `artifact.type`은 `package`, `git`, `binary`, `script`만 허용한다.
- `entrypoint.command`는 빈 문자열일 수 없다.
- `runtime.type=gpu`이면 GPU 요구사항과 GPU 가능 Target이 필요하다.
- `container`는 1차년도에서 거부한다.

## 테스트
- 유효한 CPU App 등록
- 유효한 GPU App 등록
- 필수 필드 누락
- container artifact 거부
- 중복 버전 등록 거부
