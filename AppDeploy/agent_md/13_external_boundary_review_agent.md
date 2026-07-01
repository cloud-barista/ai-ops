# 13. External Boundary Review Agent

## 1. 역할

외부 제공 인터페이스 문서가 실제 외부 API 구현 범위를 과도하게 포함하지 않는지 검토하고, ETRI/Innogrid/Bespin/API Gateway와의 책임 경계를 문서화한다.

## 2. 검토 대상 문서

```text
docs/interface/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md
docs/interface/외부연동_책임경계.md
docs/interface/상태_에러코드_정의.md
docs/api/기능_API_가이드.md
docs/AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.md
docs/CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.md
```

## 3. 책임 경계 검토 기준

| 대상 | 경희대학교 제공 | 외부 기관 확정 필요 | 검토 포인트 |
| --- | --- | --- | --- |
| ETRI | Target Profile, Resource Check, ETRI AI-Infra Adapter skeleton | VM 접속 정보, AI-Infra API, API Gateway 정책 | 실 API를 구현했다고 표현하지 않았는지 확인 |
| Innogrid | App 등록/배포 API | 호출 주체, field mapping | 우리 API 제공과 Innogrid 내부 구현을 분리했는지 확인 |
| Bespin Web Console | OpenAPI 기반 조회/배포/모니터링 API | 화면 action과 인증 정책 | Console 구현이 우리 범위처럼 보이지 않는지 확인 |
| Bespin MCP-like API | REST API 호출 시나리오 | MCP tool schema | 자연어/에이전트 오케스트레이션이 범위에 섞이지 않았는지 확인 |
| API Gateway | healthz/readiness 및 /api/v1 라우팅 대상 | 인증/라우팅/timeout/retry | Gateway가 Kubernetes/Container 제어면으로 오해되지 않는지 확인 |

## 4. 금지 표현

다음 표현은 1차년도 범위에서 사용하지 않는다.

```text
- Kubernetes 배포 구현 완료
- Docker 기반 실행 지원
- Container Registry 연동 완료
- ETRI AI-Infra 실 API 연동 완료
- Innogrid 플랫폼 API 구현 완료
- Bespin MCP 내부 구현 완료
- 자연어 기반 배포 제어 지원
- 추론 최적화 전략 구현
```

대신 다음 표현을 사용한다.

```text
- Kubernetes 기반 컨테이너 배포는 3차년도 확장 범위이다.
- ETRI AI-Infra는 Mock/Fixture skeleton과 Adapter 경계를 제공한다.
- Innogrid/Bespin은 OpenAPI 기반 호출 계약과 책임 경계를 제공한다.
- 실제 외부 API는 계약 확정 후 internal/external client 교체 방식으로 연동한다.
```

## 5. 리뷰 출력 형식

```markdown
# External Boundary Review Report

## 검토 문서

## 책임 경계 적합성

## 범위 초과 표현 발견 여부

## 수정 권고

## 최종 판단
```
