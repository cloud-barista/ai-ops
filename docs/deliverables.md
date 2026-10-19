# 역할별 코드와 산출물

## LLM 요구사항 분석

- [구현과 실행 방법](../llmop/README.md)
- [요구사항 정의](../llmop/docs/submission/requirements_definition.md)
- [기능/API 안내](../llmop/docs/submission/functional_api_guide.md)
- [LLM 비교 방법](../llmop/docs/submission/ops_llm_benchmark_method.md)
- [설계 자료](../llmop/docs/design/)
- [시험 안내](../llmop/docs/submission/test_guide.md)

## 자원 추천

- [구현과 실행 방법](../resource_ops/README.md)
- [입출력 스키마](../resource_ops/schemas/)
- [자원 정보의 출처](../resource_ops/docs/source-provenance.md)
- [상세 사용 방법](../resource_ops/docs/usage.md)

## 배포 및 운영 판단

- [구현과 실행 방법](../deployment_agent/README.md)
- [연동 흐름](../deployment_agent/docs/integration_pipeline.md)
- [API 계약](../deployment_agent/docs/openapi_deployment_agent.yaml)
- [설계 자료](../deployment_agent/docs/design/)
- [실행 코드 안내](../deployment_agent/docs/submission/execution_code_guide.md)
- [시험 안내](../deployment_agent/docs/submission/test_guide.md)
- [구조 그림](../deployment_agent/docs/images/)

초기 설계·제출 문서는 당시 개발 단계를 기록한 자료입니다. 현재 역할 경계와 통합 실행 방법은 [연동 구조](architecture.md)와 [설치 안내](getting-started.md)를 기준으로 확인합니다.

## 통합 플랫폼

- [화면과 실행 코드](../platform/README.md)
- [버튼별 사용 방법](../platform/docs/POC_BUTTON_GUIDE.md)
- [Node와 Python 시험](../platform/tests/)
- [CPU/GPU VM 실행 코드](../platform/runtime-vm/)

## 학습 정책과 실측 수집

- [보상 예측 정책](../poc/research_policy/README.md)
- [정책, 수집기, 데이터 변환과 CLI](../poc/research_policy/src/khu_policy_agent/)
- [설계/API 명세](../poc/research_policy/docs/설계_및_API_명세.md)
- [정책과 수집기 시험](../poc/research_policy/tests/)

## 최적화 연구

- [연구 개발 기록](research-development-plan.md)
- [연구 구성과 실행](../poc/optimization_research/README.md)
- [알고리즘과 재현 스크립트](../poc/optimization_research/scripts/)
- [입출력 계약](../poc/optimization_research/contracts/)
- [입력 예시](../poc/optimization_research/examples/)
- [실험 보고서](../poc/optimization_research/reports/)
- [연구 시험](../poc/optimization_research/tests/)
- [공개 데이터 출처와 라이선스](../poc/optimization_research/sources/)

개인 접속 설정, 비밀정보, 원본 DB, 대용량 외부 데이터, 모델 가중치, 실행 로그와 임시 백업은 포함하지 않습니다. 보고서의 원자료·모델·생성 결과 경로는 필요한 재현 자료를 가리키며, 해당 자료는 별도로 준비해야 합니다.
