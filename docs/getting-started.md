# 설치와 실행

## AI-App 8090 자연어 연동

8090의 자연어 프록시 `AIAPP_MOCK_NL_URL`에는 AI-App에서 접속할 수 있는 `app_automation_agent` 주소를 지정합니다. `llmop`과 `app_automation_agent`를 먼저 실행하면 Innogrid 엔진이 기동할 때 카드를 등록할 수 있습니다. 두 서비스를 호스트에서 직접 실행할 때는 아래 포트를 쓸 수 있습니다. [AI-App 연동 설정](../app_automation_agent/README.md#ai-app-자연어-연동)

```bash
PORT=18110 go -C llmop/go/service-control-api run ./cmd/llmop-api
```

```bash
PORT=18130 AIOPS_LLMOP_BASE_URL=http://127.0.0.1:18110 \
  AIOPS_AIAPP_BASE_URL=http://127.0.0.1:8090 \
  go -C app_automation_agent/go/service-control-api run ./cmd/service-control-api
```

실행 전 `llmop`에 사용할 모델 후보를 설정하고, 두 서비스에 같은 `AIOPS_OPS_LLM_TOKEN`을 설정합니다. `app_automation_agent`에는 AI-App의 bearer에 맞는 `AIOPS_AIAPP_TOKEN`과 허용할 `AIOPS_AIAPP_NAMESPACE`를 지정합니다. [후보 설정과 OpsLLM API](../llmop/go/service-control-api/README.md)

Innogrid 엔진의 `AIAPP_ENGINE_REGISTRY_URL`을 `http://127.0.0.1:18130/api/v1/integrations/ai-app`처럼 설정합니다. 컨테이너에서 연동할 때는 엔진이 접근할 수 있는 주소를 사용하고 Deployment Agent의 `AIOPS_BIND_ADDRESS`도 맞춥니다. 엔진의 `AIAPP_ENGINE_REGISTRY_TOKEN`과 Deployment Agent의 `AIOPS_AIAPP_ENGINE_REGISTRY_TOKEN`에는 같은 등록 전용 값을 지정합니다. `GET /aiapp/partner/nl/readyz`가 `200`을 반환하면 8090 콘솔에서 배포 ID를 넣어 상태를 조회합니다. [엔진 카드 등록](../app_automation_agent/README.md#innogrid-엔진-카드-등록)

AI-App을 Compose로 실행하고 Deployment Agent를 같은 호스트에서 실행할 때는 컨테이너에서 접근 가능한 호스트 리스너를 사용합니다. 이 경우 `AIAPP_MOCK_NL_URL=http://host.docker.internal:18130`과 `AIAPP_ENGINE_REGISTRY_URL=http://host.docker.internal:18130/api/v1/integrations/ai-app`을 설정합니다. Prometheus도 호스트에서 제공하면 `AIAPP_OBSERVATION_PROMETHEUS_URL`에 같은 호스트 이름을 쓸 수 있습니다.

MCP 클라이언트는 `/aiapp/partner/nl/mcp`에서 같은 자연어 에이전트를 호출합니다. 게이트웨이 bearer와 `X-AIMCMP-Namespace`를 지정한 뒤 `tools/list`로 요청 처리와 실행 기록 조회 도구를 확인합니다. [자연어 에이전트 MCP](../app_automation_agent/README.md#자연어-에이전트-mcp)

AI-App 운영 화면은 `http://127.0.0.1:8090/aiapp/ui`입니다. 이 구성에서 자연어 카탈로그·배포 상태를 조회하고 변경 제안을 기록할 수 있습니다. 관측 자원 추천이나 연구 PoC의 추천→배포 계획을 사용할 때 `resource_ops`를 추가합니다. AI-App 콘솔의 GPU 스펙·이미지 조회는 현재 설정된 CB-Tumblebug 추천 경로를 사용합니다. [기존 PoC 호출](../platform/server/upstream.mjs)

## 독립 서비스

Go는 각 모듈의 `go.mod` 기준으로 준비합니다. 전체 검증에는 Go 1.26.5, Node.js 22 이상, Python 3.10 이상을 사용합니다.

```bash
cd llmop/go/service-control-api
go run ./cmd/llmop-api
```

```bash
cd resource_ops
go run ./cmd/server
```

```bash
cd app_automation_agent/go/service-control-api
go run ./cmd/service-control-api
```

각 명령은 저장소 루트에서 별도 터미널로 실행합니다. 동시에 실행할 때는 `HOST`, `PORT`, 각 API endpoint를 설정해야 합니다. 통합 실행기의 포트는 플랫폼 18100, LLM_Op 18110, 자원 추천 18120, 배포 판단 18130, 관측 입력 18140, AppDeploy 18150, 연구 정책 18170, 판단 Agent 18185, Prometheus 18190입니다.

LLM_Op의 모델 호출에는 Ollama 또는 설정 파일에 지정된 provider가 필요합니다. API 키는 환경 변수로만 주입하며 설정 예시에 실제 값을 넣지 않습니다. 입력 예시와 후보 설정은 `llmop/examples`와 `llmop/config`를 참고하세요.

자원 추천의 Prometheus 주소는 `RESOURCE_PROMETHEUS_URL`로 지정합니다. 과거 측정 표본과 현재 가용 자원을 혼동하지 않도록 관측 유효성 검사가 적용됩니다.

## 연구 PoC API와 통합 Swagger

```bash
node platform/server/index.mjs
```

`http://127.0.0.1:18100/healthz`에서 기동 상태를 확인합니다. 역할별 API 문서는 `/swagger`에서 열고, 연구 Flow는 `/api/v1/flows`에서 조회합니다. 분석, 추천, PoC 배포에는 해당 API와 실행 모듈을 함께 준비합니다.

## Windows 통합 실행

통합 실행기는 구형 AppDeploy를 사용하는 연구 PoC를 `AIOPS_APPDEPLOY_POC_ENABLED=true`로 시작합니다. AI-App 연동은 이 문서 상단의 8090 실행 방법을 따릅니다.

1. `ai-ops`의 `AppDeployer` 브랜치에서 AppDeploy 모듈을 별도 준비합니다. 통합 실행기에는 `cmd/server/main.go`가 있는 모듈 경로를 지정합니다.
2. `platform/scripts/Setup-LocalAiRuntime.ps1`로 전용 Python 환경과 고정 BERT Tiny revision을 준비합니다. 모델 다운로드가 발생합니다.
3. `platform/scripts/Setup-Prometheus.ps1`로 로컬 Prometheus를 준비합니다.
4. 아래 연구 정책 준비를 완료합니다. 원본 학습 DB는 이 저장소에 포함하지 않습니다.
5. 저장소 루트에서 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File platform/scripts/Run-KhuResearchPoc.ps1 -Action Start -AppDeployModule "C:\path\to\AppDeploy"
```

종료는 같은 실행기의 `-Action Stop`을 사용합니다. 이 실행기가 기록한 소유 프로세스만 종료합니다. VM 정리가 확인되지 않으면 정리 감시기를 유지합니다.

## 연구 정책

`poc/research_policy/src/khu_policy_agent`에는 플랫폼이 사용한 원래 보상 예측 구현을 포함했습니다. 학습 재현에는 `runs/placement.sqlite3`, `runs/observed-lifecycle.sqlite3` 형식의 원본 실측 DB가 별도로 필요합니다.

`<policy-source-root>/src/khu_policy_agent`에 이 구현을 두고 같은 루트의 `runs` 아래 원본 DB를 준비한 후 실행합니다.

```bash
python -m pip install -e ./poc/research_policy
python platform/research_policy.py build --source-root <policy-source-root> --artifact-dir output/khu-platform-validation/research-policy
python platform/research_policy.py serve --artifact-dir output/khu-platform-validation/research-policy
```

원본 DB 없이도 Go 서비스와 플랫폼의 단위 검사는 실행할 수 있습니다. 정책 학습과 오프라인 평가는 원본 DB가 필요합니다.

## 클라우드 실행

`platform/config/cloud-connection.example.json`은 비활성 예시입니다. 실제 연결 파일은 `.gitignore`로 제외합니다. 관리 서버와 계정의 소유권·allowlist, SSH 호스트 키, AWS CLI/SSM, 이미지와 실행 모듈을 검토한 환경에서만 사용합니다.

공개 코드의 `192.0.2.216`과 `000000000000`은 문서용 값입니다. 실제 관리 환경을 배포할 때 해당 allowlist를 운영자 설정과 일치시켜야 합니다. 비용 검토, 승인, 준비 검사 및 종료 감시를 생략하지 않습니다. 기본 시험과 GitHub CI는 AWS VM을 만들지 않습니다.
