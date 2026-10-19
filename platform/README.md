# KHU Integrated PoC

경희대의 요구사항 분석, 자원 추천, 배포·운영 판단을 실제 응용 실행과 연결하는 연구용 웹 PoC입니다.

## 시연 흐름

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

기본 주소는 `http://127.0.0.1:18100/`입니다. 위 명령은 웹/API만 시작합니다. 실제 분석·배포·실측에는 각 역할의 API와 실행 모듈이 필요합니다.

Windows 통합 실행은 [`scripts/Run-KhuResearchPoc.ps1`](scripts/Run-KhuResearchPoc.ps1)을 사용하며, AppDeploy와 모델·연구 정책의 사전 준비가 필요합니다.

## 검증

```bash
node --test platform/tests/*.test.mjs
python -m unittest discover -s platform/tests -p 'test_*.py'
```

[설치와 외부 의존성](../docs/getting-started.md) / [버튼 안내](docs/POC_BUTTON_GUIDE.md) / [검증 범위](../docs/validation.md)

클라우드 설정 예시는 비활성 상태입니다. 비밀키와 실제 연결 설정은 커밋하지 않습니다.
