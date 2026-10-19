# 기존 자산 점검: 시나리오와 후속 API

## 점검 범위

2026-08-05에 origin/geon 87c82ce와 origin/AppDeployer 9b91502의 소스, 예제, 계약 문서를 정적으로 확인했다. 또한 로컬 참고자료의 `통합 다이어그램 (초안).png`(2026-07-29)와 `작업/요구사항정의서_v0.1.docx`(2026-07-10)를 읽기 전용으로 대조했다. 실제 Qwen 서버, 과금 API, AppDeployer 서버, VM은 실행하지 않았다.

## 요구사항·다이어그램 근거

| 근거 | 확인 내용 | 현재 LLM_Op 해석 |
| --- | --- | --- |
| 통합 다이어그램 초안 | 사용자/API 자연어 요청 → 입력 파싱·배포/추론 요구 분석 → ApplicationProfile, 상태·metric feedback → Safe Guard·Repair → Manifest → Common JSON/Deployment Orchestrator | 자연어와 관측값을 정규화하고 안전한 Manifest 초안을 만드는 흐름을 소유. Resource Adapter, 실제 배포, Runtime Adapter는 후속 책임 |
| OPS-02 | 사용자 요구사항과 AI반도체 클라우드 자원 상태를 바탕으로 실행 계획 생성 | 자연어·상태·로그 입력 계약과 제한된 Proposal/Manifest mapping의 직접 근거 |
| SFR-OPS-04 | 자연어 기반 운영 요청 분석 및 작업 단위 변환 | 요청 parser·Safeguard·bounded Proposal의 근거 |
| SFR-OPS-05 | AI 응용 운영 요구사항 및 자원 상태 분석 | resource/monitoring/log 정규화와 freshness·scope 검증의 근거 |
| SFR-OPS-06 | 실행 계획 생성·추천·사용자 승인 | prepare-only Manifest 초안과 명시적 handoff 경계의 근거. 승인 verifier는 아직 없음 |
| SFR-OPS-07 이후 | 배포·업데이트 자동화, 실행 제어·최적화, 이력·예외 처리 | 현재 범위 밖. geon/AppDeployer 후속 계층과의 계약 대상으로만 기록 |

다이어그램의 LLM 선택기는 현재 단계의 목표가 아니다. LLM_Op은 상위 계층이 명시한 `candidate_id`를 구성에 결합할 뿐 모델을 탐색·평가·추천하지 않는다.

요구사항정의서 표는 SFR-OPS-04~06의 구현 완료 시기를 2027로 기재한다. 현재 LLM_Op 산출물은 해당 흐름을 앞서 구체화하는 prepare-only 계약·하네스 초안이며, 공식 요구사항 완료나 실제 배포 자동화 달성을 주장하지 않는다.

| 요구사항 | 문서 완료 연도 | 현재 coverage | 현재 소유·경계 |
| --- | --- | --- | --- |
| SFR-OPS-02 | 2026 | 부분 | 기존 Planner requester allowlist와 LLM_Op prepare-only Guard를 재사용. 역할·권한·승인 정책 관리 기능 자체는 geon 범위 |
| UFR-OPS-03 / SFR-OPS-04 | 2027 | 선행 PoC·부분 | 자연어 요청 parser, Safeguard review와 별도 bounded Proposal, 작업 단위 action. 공개 사용자 왕복 API는 미구현 |
| UFR-OPS-04 / SFR-OPS-05 | 2027 | 선행 PoC·부분 | caller 제공 상태·로그·metric 분석, 명백한 모순 Guard. telemetry provenance와 실제 feasibility는 미보장 |
| UFR-OPS-04·05 / SFR-OPS-06 | 2027 | 선행 PoC·부분 | prepare-only 실행 계획·Manifest 초안. 사용자 승인 verifier와 제출은 미구현 |
| SFR-OPS-07~09 | 2028 | 범위 밖 | 실제 배포·업데이트·제어·최적화·이력·예외 처리는 geon/AppDeployer 후속 계층 책임 |

현재 지원 자원은 AppDeploy v1alpha1 계약에 맞춘 CPU, memory, NVIDIA GPU, storage의 VM-oriented subset이다. 요구사항 시나리오의 컨테이너·TPU·NPU·비용·가용성 전체를 구현한 것으로 간주하지 않는다.

## 사용자 요청 예시와 시나리오

| 자산 | 확인 내용 | 판단 |
| --- | --- | --- |
| examples/requests/run-appdeploy-planner.json | 자연어 GPU 배포 요청, app_version_id, Qwen candidate_id, polling 설정 예시 | 구현·문서화됨 |
| examples/responses/run-appdeploy-planner-success.json | Guard, Manifest, deployment_id, polling, logs, retry 추천을 포함한 성공 예시 | 문서화됨. 예시 자체가 실제 통합 증적을 의미하지는 않음 |
| examples/appdeploy/deployment-create-request.json | AppDeployer DeploymentManifest POST 본문 예시 | 문서화됨 |
| examples/appdeploy/deployment-response.json | AppDeployer가 선택한 target_profile_id와 RUNNING 상태 예시 | 문서화됨 |
| data/ops_llm_eval_scenarios.jsonl | GPU/CPU, 모호성, secret 거부, 신뢰 필드 보존, Target 경계, polling, 재시도 실패 등 10개 평가 시나리오 | 평가 기반 존재 |

기존 geon 시나리오는 자연어 요구와 배포 후 상태 처리에는 충분한 출발점이지만, 통합 다이어그램의 상태·피드백과 구조도 B의 서버 상태·Log Data를 Manifest 생성 전 단일 입력으로 결합한 예제는 없었다. LLM_Op이 `examples/llm-op/fresh-latency-request.json`과 fixture-only 두 단계 demo로 이 공백을 보완했다.

## geon의 구현·문서화 상태

| 항목 | 증적 | 상태 |
| --- | --- | --- |
| OpenAI 호환 Qwen 호출 | internal/llmclient/client.go, OpenAI 호환 Candidate 설정 예시 | 코드·설정 예시 존재 |
| 로컬 Qwen 예시 | config/ops_llm_eval_candidates.local_ollama.json의 qwen3.5:4b | 예시만 존재, 이 점검에서 서버 실행은 확인하지 않음 |
| Manifest 생성 | internal/deploymentplanner/generator.go | 코드 존재 |
| 기존 Planner 입력 | natural_language_request, app_version_id, target hint, requested_by, parameters, requirements | 상태·로그 입력 없음 |
| 요청·Manifest Guard | internal/plannerguard, Planner/ControlRun 문서 | 코드·정책 문서 존재 |
| Agent Control Common JSON | internal/agentcontrol의 ApplicationProfile → ResourceRecommendation → DesiredDeploymentSpec → DeploymentCreateRequestEnvelope | 통합 다이어그램 경로가 이미 존재. AppDeploy HTTP body와는 다른 계약이며, 새 internal/llmopbridge는 Profile minima와 선택 Resource candidate exact 단일-node 값을 분리 투영. 기본 CPU analyzer/catalog 경로는 지원하고 device-memory minimum이 있는 자연어 GPU 경로는 fail-closed. 공식 route 결합 위치는 합의 필요 |
| AppDeployer client | internal/appdeploy/client.go와 models.go | 배포·상태·로그·모니터링 모델·호출 경로 존재 |
| 공식 흐름 문서 | docs/design/integration_boundary.md, docs/design/main_llm_go_guard_control_flow.md | 문서 존재 |

## AppDeployer의 후속 API

AppDeployer 브랜치의 AppDeploy/contracts/openapi/openapi.yaml와 DeploymentManifest Schema에서 다음을 확인했다.

| 기능 | 경로 | LLM_Op 용도 | 상태 |
| --- | --- | --- | --- |
| Manifest 배포 요청 | POST /api/v1/deployments | 검증된 Manifest 제출 | OpenAPI·예제·geon client 확인 |
| 배포 상태 | GET /api/v1/deployments/{deployment_id} | 제출 후 상태 확인 | OpenAPI·geon client 확인 |
| 배포 로그 | GET /api/v1/deployments/{deployment_id}/logs | 운영 관측 입력 | OpenAPI·geon client 확인 |
| 자원 inventory | GET /api/v1/resources/inventory | resource snapshot 구성 | OpenAPI 확인 |
| 모니터링 요약 | GET /api/v1/monitoring/summary | 상태·알람 입력 | OpenAPI·geon 모델 확인 |
| Runtime health | GET /api/v1/monitoring/runtime-health | Target 상태 입력 | OpenAPI 확인 |
| 배포 metric | GET/POST /api/v1/deployments/{deployment_id}/metrics | 추후 성능 관측 | OpenAPI 확인 |

DeploymentManifest Schema는 app_version_id를 필수로 하고, target_profile_id를 선택 hint로 정의한다. 최종 Target은 AppDeployer가 준비 상태와 자원 조건을 바탕으로 선택한다. schema에는 runtime_profile_id가 없다.

## 결론과 보완 우선순위

1. 사용자 요청 예시, Manifest 예시, Planner 응답 예시, AppDeployer OpenAPI는 이미 있다.
2. AppDeployer 후속 연동 API도 계약 문서와 geon client 모델 기준으로 존재한다.
3. 다만 Qwen 실서버와 geon-LLM-AppDeployer의 최신 공동 종단간 실행 증적은 이 정적 점검 범위에서 확인되지 않았다.
4. 가장 큰 계약 공백은 상태·로그를 LLM 입력으로 정규화하는 형식, 민감정보 제거 규칙, freshness 기준, 승인 없는 제출 차단 상태다.
5. LLM_Op의 현재 코드는 이 공백을 입력 계약·normalizer·결정적 Request Guard·자연어 Safeguard review·별도 Proposal·출력 Guard로 채운다. AppDeployer 조회·제출 adapter와 승인 adapter는 아직 없으며, Target·Runtime 선택 책임도 바꾸지 않는다.
6. 실제 AppDeploy POST body는 `appdeploy.DeploymentCreateRequest{manifest}`이고 Agent Control의 Common JSON `DeploymentCreateRequestEnvelope`와 필드가 다르다. LLM_Op은 전자의 exact `prepared_request` golden과 후자의 ApplicationProfile/ResourceRecommendation을 검증하는 별도 bridge를 제공한다. bridge는 두 payload를 캐스팅하지 않으며 공식 HTTP route 결합은 geon과 합의가 필요하다.
