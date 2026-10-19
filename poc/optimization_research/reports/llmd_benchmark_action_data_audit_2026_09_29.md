# llm-d 벤치마크 수집 기능과 공개 스케일 학습 자료의 구분

2026-09-29. **판정: 수집 기능은 유망하지만, 지금 익명으로 재현 가능한 스케일 조치 학습 자료로는 NO-GO.** [기존 세 실행 감사](token_aware_autoscaling_action_feasibility.md)의 원본과 이 저장소에서 확인되지 않은 실행 산출물을 혼동하지 않는다.

## 확인한 원본

- [`llm-d-benchmark` 고정 커밋 `32e3dfb`](https://github.com/llm-d/llm-d-benchmark/tree/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e)의 [수집 코드](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/workload/harnesses/collect_metrics.sh#L108-L205)는 모델별 Deployment/StatefulSet의 desired·ready·available·updated replica 수를 `replica_status_timeseries.json`에 매번 추가한다. `start` 경로는 이 함수를 매 반복 호출한다([동일 파일](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/workload/harnesses/collect_metrics.sh#L500-L535)). 따라서 [수집 문서](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/docs/metrics_collection.md#infrastructure-metrics-kubernetes-api)의 “one-time snapshots” 설명만으로 시계열이 없다고 판정하면 틀린다. [오토스케일 안내](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/docs/token-aware-autoscaling.md#reading-a-run)도 시계열 파일을 명시한다.
- 같은 [수집 코드](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/workload/harnesses/collect_metrics.sh#L208-L335)는 발견한 실행 중 Pod의 생성·Ready 시각을 `pod_startup_times.json`에 누적한다. [요청별 분석 코드](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/llmdbenchmark/analysis/per_request_plots.py#L46-L85)는 `per_request_lifecycle_metrics.json`이 있을 때 그 `start_time`, `end_time`, 토큰 시각을 읽는다. 이는 **도구가 해당 필드를 처리할 수 있다는 증거**이지, 같은 공개 실행에 세 파일이 모두 존재한다는 증거가 아니다.
- 고정 커밋의 GitHub 재귀 트리는 완전(`truncated=false`, 855개 항목)하며 `replica_status_timeseries.json`이나 `per_request_lifecycle_metrics.json` **실행 산출물**을 포함하지 않는다. 공개 릴리스 7개에도 첨부 자산이 없다. [결과 저장소 정책](https://github.com/llm-d/llm-d-benchmark/blob/32e3dfb0250c40a292b65940d9d2abfab3fb3a6e/llmdbenchmark/results_store/SUBMISSION_POLICY.md#tiers-of-benchmark-results)은 `gs://llm-d-benchmarks/`와 staging 버킷을 가리키지만, 2026-09-29에 두 버킷의 익명 GCS 객체 목록 요청은 모두 HTTP 401이었다. 이는 **익명 목록 접근 실패**이며 객체가 없다는 증거는 아니다.
- 위 수집기는 상태를 **조회**한다. desired 값 변경의 첫 관측 시각은 HPA/KEDA의 실제 명령 발행·접수 시각이나 정책 결정 ID가 아니다. Pod 생성·Ready 시각도 명령 ID와 자동 연결되지 않는다.

## 사용할 수 있는 조건

동일 공개 실행에 대해 실제 스케일 명령/접수 로그, 위 replica 시계열과 Pod 준비 자료, 요청별 접수·완료·실패 및 배포 라우팅 정보가 함께 제공되고 접근 권리와 시계가 확인되면 다시 감사한다. 그때도 부하 변동과 정책 조치가 동시에 일어나는 벤치마크에서는 단순 전후 지연 차이를 조치의 인과 효과로 학습하지 않는다. 현재의 [경희대 사건 자료 계약](scale_action_events_v0_2_0.md)은 그대로 유효하다.
