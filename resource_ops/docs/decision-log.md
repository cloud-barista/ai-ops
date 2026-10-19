# Decision Log

## 2026-08-27: Resource Ops 역할 축소

- 입력을 ApplicationProfile/OptimizationContext에서 ResourceQuery로 변경했다.
- 출력을 OptimizationDecision/SchedulerHandoff에서 ResourceRecommendation으로 변경했다.
- server에서 `/select`, `/optimize`를 제거했다.
- deployment, scaling, feedback, manifest는 공개 runtime 경계에서 제외했다.

## 2026-08-27: 교체 가능한 Source 경계

- 외부 payload를 직접 domain으로 사용하지 않는다.
- 각 adapter는 source payload를 `RawFact[]`로 투영한다.
- Normalize 이후 필터·점수·추천은 Prometheus를 알지 못한다.

## 2026-08-27: 현재 가용량 fail-closed

- 총 용량으로 하드웨어 수용 가능성을 확인한다.
- 현재 가용량으로 즉시 추천 가능성을 추가 확인한다.
- 가용 metric 누락은 warning과 제외 사유로 표현한다.
- 후보 없음은 오류가 아니라 정상 빈 추천 결과다.
