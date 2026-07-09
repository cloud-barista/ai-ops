# Deliverables Map

이 디렉터리는 1차년도 제출 산출물과 검증 증적을 한 곳에서 찾기 위한 위치 지도이다.
개발자가 계속 갱신하는 실행 가이드와 운영 기준은 `../docs`에 둔다.

## 구성

| 위치 | 내용 |
| --- | --- |
| `design/` | 공식 구조 설계서, 프로토타입 개발설계서, 설계서 그림 |
| `interface/` | 외부 제공 인터페이스 명세, 외부 연동 책임 경계, 인터페이스 예제, contract checklist |
| `evidence/` | 증적 패키지 가이드와 자동 수집 결과 |
| `release/` | 1차년도 제출 체크리스트와 리뷰 기록 |

## 기준 경로

| 항목 | 경로 |
| --- | --- |
| 구조 설계서 | `design/AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.md` |
| 프로토타입 개발설계서 | `design/CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.md` |
| 외부 제공 인터페이스 명세 | `interface/spec/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md` |
| 외부 인터페이스 예제 | `interface/examples/requests`, `interface/examples/responses` |
| 외부 책임 경계 | `interface/external/외부_연동_경계_정리.md` |
| 시험 증적 결과 | `evidence/results/{timestamp}` |
| 제출 체크리스트 | `release/1차년도_제출_패키지_체크리스트.md` |

OpenAPI source of truth는 계속 `../contracts/openapi/openapi.yaml`이다.
