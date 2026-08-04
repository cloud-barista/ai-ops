# VS Code Local Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 저장소를 VS Code로 연 개발자가 `F5` 또는 Run Task만으로 geon Agent Control을 18080에서 실행하고 검증할 수 있게 한다.

**Architecture:** VS Code의 Go launch configuration과 shell task가 같은 작업 디렉터리와 환경 변수를 공유한다. README는 VS Code 빠른 실행을 첫 진입 경로로 제공하고 CLI 실행, Runtime Agent, Mock/실제 배포 경계를 후속 설명으로 분리한다.

**Tech Stack:** VS Code Go extension, JSON launch/tasks configuration, Go 1.25+, Markdown, PowerShell JSON validation

## Global Constraints

- 기존 `geon` 브랜치에서만 작업한다.
- AppDeploy 코드는 변경하지 않는다.
- 기본 주소는 `127.0.0.1:18080`이다.
- 기본 Adapter는 `mock`이며 결과는 `SIMULATED`이다.
- Ollama는 선택적인 Qwen 비교 실험에만 필요하다.
- 실제 VM, Runtime Agent 프로세스, credential을 자동 생성하거나 저장하지 않는다.
- `config/inference_optimization.json`과 `tmp/`는 수정하거나 커밋하지 않는다.

---

### Task 1: VS Code F5 및 Task 실행 구성

**Files:**
- Create: `.vscode/launch.json`
- Create: `.vscode/tasks.json`
- Create: `.vscode/extensions.json`

**Interfaces:**
- Consumes: 저장소 루트 `${workspaceFolder}`와 설치된 `go` 명령
- Produces: `geon: Agent Control (18080)` debug configuration, 서버·테스트·vet Task

- [ ] **Step 1: VS Code 구성 파일을 추가한다**

`launch.json`은 `go/service-control-api/cmd/service-control-api`를 Go launch 모드로 실행하고 다음 환경을 전달한다.

```json
{
  "AIOPS_REPO_ROOT": "${workspaceFolder}",
  "AIOPS_BIND_ADDRESS": "127.0.0.1",
  "AIOPS_DEPLOYMENT_ADAPTER": "mock",
  "PORT": "18080"
}
```

`tasks.json`은 같은 환경의 서버 실행과 `go test ./... -count=1`, `go vet ./...`을 `go/service-control-api`에서 실행한다.

- [ ] **Step 2: JSON 파싱 검증을 실행한다**

Run:

```powershell
Get-Content .vscode/launch.json -Raw | ConvertFrom-Json | Out-Null
Get-Content .vscode/tasks.json -Raw | ConvertFrom-Json | Out-Null
Get-Content .vscode/extensions.json -Raw | ConvertFrom-Json | Out-Null
```

Expected: 세 명령 모두 오류 없이 종료한다.

- [ ] **Step 3: 구성 파일을 커밋한다**

```bash
git add .vscode/launch.json .vscode/tasks.json .vscode/extensions.json
git commit -m "dev: add VS Code local run configuration"
```

### Task 2: README 실행 안내 재구성

**Files:**
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`

**Interfaces:**
- Consumes: Task 1의 launch name과 task label
- Produces: 처음 clone한 개발자가 따라 할 수 있는 VS Code·CLI 실행 및 오류 해결 절차

- [ ] **Step 1: 루트 README의 빠른 실행을 VS Code 중심으로 재작성한다**

다음 순서를 문서 첫 실행 경로로 제공한다.

```text
git clone --branch geon --single-branch ...
VS Code에서 저장소 루트 열기
golang.go 설치
F5 → geon: Agent Control (18080)
http://127.0.0.1:18080/healthz
http://127.0.0.1:18080/
```

CLI 실행, Mock 경계, Ollama 선택 사용, Runtime Agent endpoint 별도 실행 조건을 후속 절로 유지한다.

- [ ] **Step 2: Service Control README에 동일한 실행 계약을 반영한다**

VS Code launch와 Task 이름을 실제 JSON과 동일하게 적고, 포트 충돌과 Go PATH 문제 해결 명령을 추가한다.

- [ ] **Step 3: 문서 일관성을 확인하고 커밋한다**

Run:

```powershell
rg -n "geon: Agent Control \(18080\)|geon: 서버 실행|geon: 전체 테스트|SIMULATED|Runtime Agent" README.md go/service-control-api/README.md
git diff --check
```

Expected: 두 README에 실행 이름과 경계 설명이 존재하고 whitespace 오류가 없다.

```bash
git add README.md go/service-control-api/README.md
git commit -m "docs: add VS Code quick start"
```

### Task 3: 저장소 전체 검증

**Files:**
- Verify only

**Interfaces:**
- Consumes: Task 1과 Task 2 결과
- Produces: 공유 가능한 실행 구성의 검증 증거

- [ ] **Step 1: Go 테스트와 정적 검사를 실행한다**

Run:

```powershell
cd go/service-control-api
go test ./... -count=1
go vet ./...
go build ./...
```

Expected: 모든 명령이 exit code 0으로 종료한다.

- [ ] **Step 2: 개발 서버를 VS Code와 동일한 환경으로 실행해 health를 확인한다**

Expected:

```json
{"service":"service-control-api","status":"ok"}
```

- [ ] **Step 3: Git 범위를 확인한다**

Run:

```powershell
git status --short
git log -4 --oneline
```

Expected: 사용자 소유 untracked 파일만 남고 VS Code와 README 변경은 커밋돼 있다.
