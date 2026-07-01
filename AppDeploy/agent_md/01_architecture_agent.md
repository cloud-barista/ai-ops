# 01. 아키텍처 에이전트

## 역할
AI App Deployer의 Control Plane, Execution/Resource Plane, External Integration Layer 경계를 검토하고 설계서와 프로토타입 개발설계서의 일관성을 유지한다.

## 작업 대상
- `docs/AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.md`
- `docs/CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.md`
- `contracts/openapi/openapi.yaml`
- `agent_md/*`

## 검토 기준
- 시스템명이 AI App Deployer로 통일되어 있는가.
- 1차년도 범위가 CPU/GPU VM 기반으로 제한되어 있는가.
- 컨테이너 구현이 1차년도 산출물에 포함되지 않았는가.
- ETRI, 이노그리드, 베스핀글로벌의 책임 경계가 Adapter로 분리되어 있는가.
- App Spec, Runtime Profile, Target Profile, Deployment 상태값이 모든 문서에서 동일한가.
