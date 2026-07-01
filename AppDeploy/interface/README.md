# KHU AI App Deployer Interface Package

본 패키지는 경희대학교 1차년도 담당 범위인 `AI 반도체 기반 AI 응용 배포 및 운용 구조 설계`와 `CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입 개발` 중 외부 제공 인터페이스를 정리한 산출물이다.

## 구성

```text
khu_ai_app_deployer_interface_latest/
├── docs/
│   └── interface/
│       ├── KHU_AI_App_Deployer_외부제공인터페이스_명세서.docx
│       ├── KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
│       ├── 외부연동_책임경계.md
│       └── 상태_에러코드_정의.md
├── contracts/
│   └── openapi/
│       └── openapi.yaml
├── examples/
│   ├── requests/
│   └── responses/
├── tests/
│   └── interface-contract-test-checklist.md
└── agent_md/
    ├── 10_interface_common_contract.md
    ├── 11_openapi_interface_update_agent.md
    ├── 12_interface_examples_contract_test_agent.md
    └── 13_external_boundary_review_agent.md
```

## 사용 순서

1. `docs/interface/KHU_AI_App_Deployer_외부제공인터페이스_명세서.docx`로 외부 제공 범위를 검토한다.
2. `contracts/openapi/openapi.yaml`을 실제 구현 OpenAPI와 대조한다.
3. `examples/requests`와 `examples/responses`를 smoke/contract test에 연결한다.
4. `tests/interface-contract-test-checklist.md`를 기준으로 인터페이스 수용성을 확인한다.
5. 에이전트 개발 시 `agent_md/10_interface_common_contract.md`를 먼저 읽고 역할별 지시서를 따른다.

## 1차년도 제외 유지

- Docker/Kubernetes/Container 기반 배포 구현
- Kubernetes Control Plane 연동 구현
- 실제 ETRI/Innogrid/Bespin 내부 API 구현
- LLM 운영관리, Agent Interface, 자연어 기반 배포 제어, 추론 최적화 전략
