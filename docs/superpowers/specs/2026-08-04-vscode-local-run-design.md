# VS Code Local Run Design

## Goal

다른 개발자가 `geon` 브랜치를 clone한 뒤 저장소 루트를 VS Code로 열고, 별도 환경 변수 입력 없이 Agent Control PoC를 실행하고 검증할 수 있게 한다.

## User Flow

1. 저장소 루트를 VS Code로 연다.
2. VS Code가 권장하는 Go 확장 `golang.go`를 설치한다.
3. **Run and Debug**에서 `geon: Agent Control (18080)`을 선택하고 `F5`를 누른다.
4. `http://127.0.0.1:18080/healthz`와 `http://127.0.0.1:18080/`을 확인한다.
5. 필요하면 **Terminal > Run Task**에서 전체 Go 테스트를 실행한다.

## VS Code Configuration

- `.vscode/launch.json`
  - Go `launch` 구성으로 `go/service-control-api/cmd/service-control-api`를 실행한다.
  - 작업 디렉터리는 `go/service-control-api`로 고정한다.
  - `AIOPS_REPO_ROOT=${workspaceFolder}`를 전달한다.
  - `AIOPS_BIND_ADDRESS=127.0.0.1`, `PORT=18080`을 전달한다.
  - `AIOPS_DEPLOYMENT_ADAPTER=mock`을 기본값으로 사용한다.
- `.vscode/tasks.json`
  - 동일한 환경으로 서버를 실행하는 Task를 제공한다.
  - `go test ./... -count=1`과 `go vet ./...` Task를 제공한다.
- `.vscode/extensions.json`
  - Go 확장 `golang.go`를 권장한다.

## Documentation

루트 README와 Service Control README에 다음 내용을 추가한다.

- VS Code에서 저장소 루트를 여는 방법
- F5 실행 순서
- Task 실행 순서
- 18080 포트 충돌 확인 방법
- Ollama는 Qwen 비교 실험에서만 필요하다는 경계
- Mock Adapter의 `SIMULATED` 결과는 실제 VM 배포가 아니라는 경계

## Error Handling

- `go` 명령을 찾지 못하면 Go 설치 및 VS Code 재시작을 안내한다.
- 18080 포트가 이미 사용 중이면 기존 geon 프로세스를 종료하거나 `launch.json`의 `PORT`를 변경하도록 안내한다.
- Runtime Agent는 등록만으로 실행되지 않으며, 선택한 Runtime Agent endpoint는 별도로 실행돼야 함을 문서에 명시한다.

## Verification

- 세 `.vscode` 파일을 JSON으로 파싱한다.
- VS Code launch 구성과 shell Task가 동일한 환경 변수와 작업 디렉터리를 사용하는지 확인한다.
- `go test ./... -count=1`과 `go vet ./...`를 실행한다.
- 18080에서 `/healthz`가 `status=ok`를 반환하는지 확인한다.

## Scope Boundary

- AppDeploy 코드는 변경하지 않는다.
- Ollama를 자동 실행하거나 설치하지 않는다.
- 실제 VM 또는 외부 Runtime Agent를 자동으로 생성하지 않는다.
- 사용자 로컬 파일과 credential은 VS Code 설정에 포함하지 않는다.
