# 설치와 실행

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
cd deployment_agent/go/service-control-api
go run ./cmd/service-control-api
```

각 명령은 저장소 루트에서 별도 터미널로 실행합니다. 동시에 실행할 때는 `HOST`, `PORT`, 각 API endpoint를 설정해야 합니다. 통합 실행기의 포트는 플랫폼 18100, LLM_Op 18110, 자원 추천 18120, 배포 판단 18130, 관측 입력 18140, AppDeploy 18150, 연구 정책 18170, 판단 Agent 18185, Prometheus 18190입니다.

LLM_Op의 모델 호출에는 Ollama 또는 설정 파일에 지정된 provider가 필요합니다. API 키는 환경 변수로만 주입하며 설정 예시에 실제 값을 넣지 않습니다. 입력 예시와 후보 설정은 `llmop/examples`와 `llmop/config`를 참고하세요.

자원 추천의 Prometheus 주소는 `RESOURCE_PROMETHEUS_URL`로 지정합니다. 과거 측정 표본과 현재 가용 자원을 혼동하지 않도록 관측 유효성 검사가 적용됩니다.

## 웹/API만 확인

```bash
node platform/server/index.mjs
```

`http://127.0.0.1:18100/`에서 화면을 확인할 수 있습니다. 다른 API를 시작하지 않았다면 분석, 추천, 배포 등의 동작은 연결 오류를 표시합니다. 실행된 것처럼 표시하거나 모의 결과로 자동 대체하지 않습니다.

## Windows 통합 실행

1. 공식 `ai-ops`의 `AppDeployer` 브랜치에서 AppDeploy 모듈을 별도 준비합니다. 통합 실행기는 `cmd/server/main.go`가 있는 모듈 경로를 요구합니다. 다른 팀의 실행 모듈을 이 폴더에 복제하지 않습니다.
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

DB가 없는 환경에서도 Go 서비스와 플랫폼의 오프라인 단위 검사는 실행할 수 있습니다. 학습 결과나 실측 자료를 임의 생성하여 검증된 연구 결과로 표시하지 않습니다.

## 클라우드 실행

`platform/config/cloud-connection.example.json`은 비활성 예시입니다. 실제 연결 파일은 `.gitignore`로 제외합니다. 관리 서버와 계정의 소유권·allowlist, SSH 호스트 키, AWS CLI/SSM, 이미지와 실행 모듈을 검토한 환경에서만 사용합니다.

공개 코드의 `192.0.2.216`과 `000000000000`은 문서용 값입니다. 실제 관리 환경을 배포할 때 해당 allowlist를 운영자 설정과 일치시켜야 합니다. 비용 검토, 승인, 준비 검사 및 종료 감시를 생략하지 않습니다. 기본 시험과 GitHub CI는 AWS VM을 만들지 않습니다.
