# KHU Integrated PoC

경희대의 요구사항 분석, 자원 추천, 배포·운영 판단을 실제 응용 실행과 연결하는 연구용 PoC API입니다. AI-App 연동의 운영 화면은 8090 콘솔을 사용합니다. [AI-App 연동](../docs/getting-started.md#ai-app-8090-자연어-연동)

## 연구 Flow

1. LLM 선택과 요구사항 분석
2. 자원 추천과 실행 대상 선택
3. 배포 판단 및 Guard 검증
4. 별도 승인 후 응용 실행
5. 실제 추론, 관측과 운영 판단
6. 동일 실험의 결과·실행 기록 확인

## 실행

```bash
node platform/server/index.mjs
```

기동 상태는 `http://127.0.0.1:18100/healthz`에서 확인하고, 저장된 Flow는 `GET /api/v1/flows`로 조회합니다. 분석·배포·실측에는 각 역할의 API와 실행 모듈이 필요합니다.

통합 [Swagger UI](http://127.0.0.1:18100/swagger)는 LLM_Op, Resource Ops, Deployment Agent의 요청·응답 모델과 오류 응답을 한 화면에 표시합니다. `Try it out`은 기존 역할별 API 연결 설정을 사용해 요청을 전달합니다. 통합 명세는 `/openapi.json`에서 내려받으며, 화면의 JS/CSS를 불러오려면 인터넷 연결이 필요합니다.

원본 명세를 변경하면 저장소 루트에서 `python platform/scripts/generate-openapi.py`를 실행한 뒤 플랫폼을 재시작합니다. PyYAML은 `poc/optimization_research/requirements.txt`에 포함되어 있습니다.

GitHub Pages 공개용 파일은 `python platform/scripts/generate-openapi.py --pages`로 생성합니다. `output/swagger-pages/`에는 조회 전용 `index.html`과 `openapi.json`만 포함됩니다. 저장소의 Pages 소스를 GitHub Actions로 선택하면 `.github/workflows/swagger-pages.yml`이 `main`의 문서 변경 시 자동 배포합니다.

Windows 통합 실행은 [`scripts/Run-KhuResearchPoc.ps1`](scripts/Run-KhuResearchPoc.ps1)을 사용하며, AppDeploy와 모델·연구 정책의 사전 준비가 필요합니다.

## 검증

```bash
node --test platform/tests/*.test.mjs
python -m unittest discover -s platform/tests -p 'test_*.py'
```

[설치와 외부 의존성](../docs/getting-started.md) · [이전 운영 화면 기록](docs/POC_BUTTON_GUIDE.md) · [검증 범위](../docs/validation.md)

클라우드 설정 예시는 비활성 상태입니다. 비밀키와 실제 연결 설정은 커밋하지 않습니다.
