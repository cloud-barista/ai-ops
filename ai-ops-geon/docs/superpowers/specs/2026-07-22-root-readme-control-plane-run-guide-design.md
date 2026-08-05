# Root README geon Control Plane 실행 가이드 설계

## 목적

저장소를 처음 접한 사용자가 최상위 `README.md`만 읽고도 geon Control Plane의 구성 요소를 이해하고 로컬에서 실행할 수 있도록 빠른 실행 절차를 제공한다.

## 선택한 방식

최상위 README에는 실행에 필요한 최소 완결 절차를 제공하고, 세부 사용법과 Autonomous Loop 실험은 기존 `go/service-control-api/README.md`로 연결한다. 상세 문서를 그대로 복제하지 않아 두 문서의 내용이 서로 달라지는 문제를 줄인다.

## README 구성

`개요` 다음에 `geon Control Plane 빠른 실행` 섹션을 추가한다.

1. 구성 요소와 기본 포트
   - Ollama/Qwen: `127.0.0.1:11434`
   - AppDeploy: `127.0.0.1:8080`
   - geon Agent Control: `127.0.0.1:18080`
2. 사전 준비
   - Go 설치 및 Git Bash PATH 설정
   - Ollama 설치와 `qwen3.5:4b` 준비
3. 실행 순서
   - Ollama 확인
   - AppDeploy 실행
   - geon 환경변수 설정 및 실행
4. 상태 및 웹 화면 확인
   - 각 health endpoint 확인
   - AppDeploy와 geon 웹 주소 제공
5. 첫 사용 흐름
   - AppDeploy에서 Mock Target 및 App 등록
   - `app_version_id` 복사
   - geon에서 Agent/Guard 확인 후 자연어 배포 요청
   - Guard, 배포 상태, 로그, Feedback 확인
6. 종료와 상세 가이드 링크
   - 실행 터미널에서 `Ctrl+C`
   - 상세 실행 및 Autonomous Loop 문서 연결

## 안전성과 책임 경계

- geon은 Qwen 계획 생성, Go Guard 검증, AppDeploy 연동과 자율 운영 제어를 담당한다고 명시한다.
- AppDeploy는 App/Target 등록과 실제 배포 실행을 담당한다고 명시한다.
- CB-Tumblebug 또는 CSP VM을 geon이 직접 생성한다고 표현하지 않는다.
- 비밀키나 관리자 토큰을 README 명령에 직접 넣지 않는다.
- 기본 bind 주소는 `127.0.0.1`로 유지한다.

## 검증 기준

- Markdown 코드 블록과 상대 링크가 올바르다.
- 환경변수와 포트가 실제 서비스 기본값과 일치한다.
- Windows Git Bash에서 사용할 수 있는 명령 형식이다.
- 기존 상세 실행 가이드와 상충하지 않는다.
- `git diff --check`가 통과한다.
