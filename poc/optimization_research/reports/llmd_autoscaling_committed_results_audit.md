# llm-d 오토스케일 벤치마크 공개 실행물의 조치 연결 감사

2026-09-29. **판정: `PUBLIC_ACTION_READY_JOIN_NO_GO`.** 이전 [llm-d-benchmark 수집 코드 감사](llmd_benchmark_action_data_audit_2026_09_29.md)와 달리, [llm-d-autoscaling 고정 커밋](https://github.com/llm-d/llm-d-autoscaling/tree/3c06c8814bcde4f7da0c7b4fadeed015e135a9de/benchmark/results)에는 실제 **실행 결과 묶음 세 개**가 공개돼 있다. 세 묶음의 요청별 결과를 직접 열어 확인했지만, 같은 실행의 스케일 명령·replica Ready 사건이나 replica 시계열이 없어 실제 `SCALE_OUT/IN` 효과 학습에는 아직 쓸 수 없다.

## 직접 확인한 파일과 정답

고정 Git 트리는 `benchmark/results/` 아래 blob 48개와 `workspace.tar.zst` 세 개를 갖는다. [재현 감사 코드](../scripts/audit_llmd_autoscaling_public_results.py)는 세 압축본의 SHA-256을 확인하고 멤버 이름·요청별 JSON·요약 JSON·실행 메타데이터를 검사한다. 각 압축본의 `per_request_lifecycle_metrics.json`에는 `start_time`, `end_time`, `request`, `response`, `info`, `error`가 있다. 프롬프트와 응답 원문은 연구 아티팩트에 보존하지 않고 집계만 남겼다.

| 공개 실행 세션 | 요청 | 성공 | 실패 유형 | 부하 설정 |
| --- | ---: | ---: | --- | --- |
| 2026-09-03 첫 실행 | 120 | 59 | `TimeoutError` 61 | 무작위 1024 입력/출력 토큰, 일정 1요청/초 |
| 2026-09-03 두 번째 | 120 | 59 | `TimeoutError` 61 | 동일 |
| 2026-09-08 | 120 | 59 | `TimeoutError` 61 | 동일 |

총 **360요청·177성공·183타임아웃**이다. 세 실행의 모델 표기는 `Qwen/Qwen3-32B`, namespace는 `lionel-bench-model-sim-32b-pd`다. `run_metadata.yaml`에는 각 하네스 시작/종료 UTC 시각이 있지만, 요청별 `start_time`/`end_time`에는 같은 형식의 UTC 시각과 연결 키가 없다. 이 자료는 요청 실패와 지연의 작은 공개 표본으로는 읽을 수 있으나, 새로운 AI 모델·VM 형상 일반화를 위한 충분한 독립 학습 집합은 아니다.

세 압축본의 멤버는 요청별·요약 JSON, 설정 YAML, 실행 로그와 분석 디렉터리뿐이다. 커밋된 결과 트리에는 조치 발행/접수 사건, `decision_id`/`action_id`, Pod 생성·Ready, desired/available/ready replica 시계열이 없다. [벤치마크 보고 도구 설명](https://github.com/llm-d/llm-d-autoscaling/blob/3c06c8814bcde4f7da0c7b4fadeed015e135a9de/benchmark/docs/benchmark-report.md)은 replica·HPA/KEDA·요청 지연을 **별도 Prometheus/Grafana에서 조회**한다고 설명한다. 9월 8일 결과의 `grafana_snapshot.yaml`에는 `http://localhost:3000/...` 주소만 기록됐고, 그 시계열 내용은 이 커밋에 포함되지 않았다. 따라서 공개 압축본의 요청 결과와 조치/Ready를 동일 실행·동일 시계로 결합할 수 없다.

[집계 감사 JSON](../artifacts/llmd_autoscaling_public_results_audit_v1/audit.json)은 고정 커밋, 세 원본 SHA-256, 파일 목록, 설정과 필드/사건 수를 저장한다. `python scripts/audit_llmd_autoscaling_public_results.py`로 원본을 다시 받아 검사할 수 있다. 이 NO-GO는 **해당 커밋의 세 공개 실행물**에 한정한다. 별도 클러스터에 보관된 Prometheus나 비공개 실행물의 존재를 부정하지 않는다. 후속 자료가 제공되면 [사건 계약](scale_action_events_v0_2_0.md)의 실제 발행 시각·Ready·안정적인 서비스/배포 ID·조치 전후 요청 결과를 먼저 결합하고, 그다음 정책 학습 적격성을 다시 판단한다.
