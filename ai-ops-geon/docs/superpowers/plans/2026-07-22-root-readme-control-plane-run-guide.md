# Root README geon Control Plane Run Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최상위 README만 읽고도 Ollama, AppDeploy, geon Agent Control을 순서대로 실행하고 첫 배포 제어 시험을 시작할 수 있는 빠른 실행 가이드를 추가한다.

**Architecture:** 최상위 README에는 필수 명령과 확인 절차를 완결된 빠른 시작으로 제공한다. 상세 화면 사용법, Autonomous Loop, 삭제 기능과 원격 운영 보안은 기존 `go/service-control-api/README.md`를 단일 상세 문서로 유지한다.

**Tech Stack:** Markdown, Windows Git Bash, Go, Ollama, Qwen 3.5 4B, AppDeploy, geon service-control API

## Global Constraints

- 기본 Planner 모델은 `qwen3.5:4b`이다.
- 기본 주소는 Ollama `127.0.0.1:11434`, AppDeploy `127.0.0.1:8080`, geon `127.0.0.1:18080`이다.
- geon은 Qwen 계획, Go Guard 검증, AppDeploy 연동과 자동화 제어를 담당한다.
- AppDeploy는 App/Target 등록과 실제 배포 실행을 담당한다.
- 비밀키와 관리자 토큰을 README 명령에 직접 포함하지 않는다.
- 기본 bind 주소는 `127.0.0.1`로 유지한다.

---

### Task 1: 최상위 README 빠른 실행 가이드

**Files:**
- Modify: `README.md:17`
- Reference: `go/service-control-api/README.md`
- Reference: `docs/superpowers/specs/2026-07-22-root-readme-control-plane-run-guide-design.md`

**Interfaces:**
- Consumes: 기존 AppDeploy `/api/v1/healthz`, Ollama `/api/tags`, geon `/healthz` 상태 확인 endpoint
- Produces: 최상위 README의 `geon Control Plane 빠른 실행` 섹션과 상세 가이드 링크

- [ ] **Step 1: 최상위 README에 실행 섹션 추가**

`## 🎯 담당 범위` 앞에 다음 내용을 추가한다.

````markdown
## 🚀 geon Control Plane 빠른 실행

geon Control Plane은 자연어 운영 요청을 Qwen으로 계획하고, Go Request Guard와 Go Manifest Guard로 검증한 뒤 AppDeploy에 전달합니다. 세 구성 요소는 별도 프로세스로 실행합니다.

| 구성 요소 | 역할 | 기본 주소 |
| --- | --- | --- |
| Ollama | Qwen `qwen3.5:4b` 추론 | `http://127.0.0.1:11434/` |
| AppDeploy | App/Target 등록과 실제 배포 실행 | `http://127.0.0.1:8080/` |
| geon Agent Control | Agent Registry, Qwen 계획, Go Guard, 자동화 제어 | `http://127.0.0.1:18080/` |

### 1. 사전 준비

- Go 1.25 이상
- Ollama와 Qwen `qwen3.5:4b`
- AppDeploy 브랜치 저장소와 geon 브랜치 저장소

Windows Git Bash에서 Go 경로와 Qwen 모델을 확인합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
go version

ollama list
ollama pull qwen3.5:4b  # 목록에 없을 때만 실행
```

Git Bash에서 `ollama`를 찾지 못하면 `"$HOME/AppData/Local/Programs/Ollama/ollama.exe"`를 사용합니다.

### 2. AppDeploy 실행

첫 번째 터미널에서 AppDeploy 저장소 경로를 지정합니다. 다른 위치에 복제했다면 `APPDEPLOY_ROOT`만 변경합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export APPDEPLOY_ROOT="$HOME/ai-ops-AppDeployer"

cd "$APPDEPLOY_ROOT/AppDeploy"
go mod download
go run ./cmd/web
```

### 3. geon Agent Control 실행

두 번째 터미널에서 geon 저장소 경로를 지정합니다. 다른 위치에 복제했다면 `AIOPS_REPO_ROOT`만 변경합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export AIOPS_REPO_ROOT="$HOME/ai-ops-geon"
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
export AIOPS_PLANNER_GUARD_POLICY_PATH="config/planner_guard_policy.json"
export AIOPS_APPDEPLOY_BASE_URL="http://127.0.0.1:8080/api/v1"
export AIOPS_BIND_ADDRESS="127.0.0.1"
export PORT=18080

cd "$AIOPS_REPO_ROOT/go/service-control-api"
go mod download
go run ./cmd/service-control-api
```

### 4. 상태와 웹 화면 확인

세 번째 터미널에서 확인합니다.

```bash
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8080/api/v1/healthz
curl http://127.0.0.1:18080/healthz
```

- AppDeploy: `http://127.0.0.1:8080/`
- geon Agent Control: `http://127.0.0.1:18080/`

### 5. 첫 사용 순서

1. AppDeploy에서 Mock Target을 등록합니다.
2. 테스트 App을 등록하고 `app_version_id`를 복사합니다.
3. geon의 **Agents & Guard**에서 Agent와 허용 Action을 확인합니다.
4. **Deployment Planner**에 자연어 요청과 `app_version_id`를 입력합니다.
5. **Generate & Deploy**를 실행합니다.
6. Request Guard, Qwen 결과, Manifest Guard, AppDeploy 배포 상태와 로그를 확인합니다.
7. 실행 결과는 **Feedback**, 자율 운영 판단은 **Autonomous Loop**에서 확인합니다.

각 서버는 실행한 터미널에서 `Ctrl+C`로 종료합니다. Autonomous Loop, Guarded Auto, 기록 삭제와 문제 해결 절차는 [geon Agent Control 상세 실행 가이드](go/service-control-api/README.md#geon-agent-control-실행-가이드)를 참고합니다.
````

- [ ] **Step 2: 문서 구조와 값 검증**

Run:

```powershell
Select-String -Path README.md -Encoding utf8 -Pattern '^## 🚀 geon Control Plane 빠른 실행','127.0.0.1:11434','127.0.0.1:8080','127.0.0.1:18080','qwen3.5:4b'
git diff --check
```

Expected: 새 섹션과 필수 값이 검색되고 `git diff --check`가 오류 없이 종료된다.

- [ ] **Step 3: 상대 링크와 기존 상세 가이드 일관성 검증**

Run:

```powershell
Test-Path 'go\service-control-api\README.md'
Select-String -Path 'go\service-control-api\README.md' -Encoding utf8 -Pattern 'AIOPS_APPDEPLOY_BASE_URL','AIOPS_BIND_ADDRESS','PORT=18080'
```

Expected: `Test-Path`는 `True`이고 상세 가이드에서 세 환경변수가 검색된다.

- [ ] **Step 4: 변경 커밋**

```bash
git add README.md docs/superpowers/plans/2026-07-22-root-readme-control-plane-run-guide.md
git commit -m "docs: add geon control plane quick start"
```
