# FCA SQLite의 실행 방식 선택 라벨 적격성 감사

2026-09-29. 기존 SQLite에서 아직 학습하지 않은 **실측 판단 정답**을 찾기 위한 읽기 전용 감사다. 모델 학습·점수 채점은 하지 않았다.

## 결론

현재 SQLite에는 AI-App의 필요 VM 형상이나 실제 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN` 전이 효과가 없다. 새 과제로 쓸 수 있는 가장 분명한 *인접* 라벨은 FCA의 **연합 학습(FL)과 분할 학습(SL)을 각각 실행했을 때의 완료시간**이다. 같은 `group_id`에 두 방식이 모두 있는 작업은 호스트별 26개, 합계 52개 실행쌍이다. 빠른 방식은 64 호스트에서 FL 16/SL 10, 216 호스트에서 FL 17/SL 9로 실제 변한다. 반면 분산 추론의 CPU/GPU 선택은 배치 p95 기준 GPU가 64에서 40/40, 216에서 39/40 우세해 이 자료에서 비자명한 선택 과제가 아니다.

FL/SL은 **학습 실행 방식**이며, 경희대의 AI-App VM 형상 추천·배포·복제 수 조정과 다른 결정이다. 이를 연구한다면 “주어진 FCA 학습 작업의 성공한 FL/SL 중 관측 완료시간이 짧은 방식”으로만 이름 붙인다. 사용자 지연 SLO, 학습 품질, VM 비용, 실제 운영 조치 효과는 평가할 수 없다. 표본이 작고 5종 모델의 일부 입력형상·한 날짜·두 호스트에 한정되어, 모델을 훈련하더라도 탐색적 결과로만 보고해야 한다.

## 원본·스키마·정답

| 원본 SQLite | SHA-256 | 연합 학습 그룹 / 실행 | FL·SL 완전쌍 | 빠른 방식 |
| --- | --- | ---: | ---: | --- |
| `/path/to/aiops/runs/fca-hf-text-matrix-64/executions.sqlite3` | `76c7f65cb44544645210027bd96a458f553c76bcf0e6f7b94d92f77374c07e11` | 48 / FL 24 + SL 13 (장치별; 전체 74) | 26 | FL 16, SL 10 |
| `/path/to/aiops/runs/fca-hf-text-matrix-216/executions.sqlite3` | `96ea46fbc633a8f1f0a05da9c255f72cac45d91ceffe2730f3a15b8947f88edf` | 48 / FL 24 + SL 13 (장치별; 전체 74) | 26 | FL 17, SL 9 |

표의 “FL 24 + SL 13”은 **CPU와 GPU 각각**의 실행 수다. 각 호스트의 총 `scenario_experiments`는 154개이고, 그중 연합 학습 74개, 분산 추론 `PARTITIONED` 80개다. 연합 학습 48그룹 중 26그룹만 두 방식이 모두 있으며, 나머지 22그룹은 단일 방식만 관측했다. 완전쌍은 모두 `terminal=1`, `success=1`, `status='completed'`, `comparison_complete=1`이고 `wall_duration_ms`가 있다. `scenario_groups.best_mode`는 실제 성공한 방식 중 `wall_duration_ms` 최솟값으로 사후 계산 (`/path/to/aiops/federated_coordination_agent/live_scenario_store.py:428`)된다. 52쌍 모두 두 완료시간에서 이를 다시 계산한 값과 일치하며 동률은 없다. 따라서 `best_mode`는 정답으로만 쓸 수 있고 입력으로 쓰면 누출이다.

실행 전 정보는 `scenario_groups`의 `model_id`, `model_size`, `variant`, `device`, `context_json`과 수집기의 작업 프로필 (`/path/to/aiops/federated_coordination_agent/live_scenario_runner.py:303`)에 있다. `context_json`은 그룹 생성 시, 제출 전에 기록됐으며 쌍의 두 방식에서 동일하다. 다만 `context_json.clients.expected_training_time_*`, `expected_inference_latency_ms`, 가용성 등에는 생성 프로필·모의치가 섞여 있다. 실측 결과를 예측하는 모델에서는 출처가 확인된 모델 크기·입력 길이·배치·장치·호스트만 허용하고, 생성 예상 시간·사후 보상·실행/자원 샘플·`best_mode`는 배제해야 한다. `scenario_experiments.wall_duration_ms`는 제출부터 터미널 상태까지 runner가 관측한 벽시계 시간 (`/path/to/aiops/federated_coordination_agent/live_scenario_runner.py:519`)이다. VM 부팅·이미지 배포시간이 아니다.

## 분할과 교란

- 완전쌍의 모델은 5종이다. 호스트별 CPU 13쌍·GPU 13쌍이며, 빠른 방식은 64에서 CPU FL 11/SL 2, GPU FL 5/SL 8이고 216에서 CPU FL 12/SL 1, GPU FL 5/SL 8이다. “CPU면 FL, GPU면 SL”만 고르는 단순 규칙도 각 호스트에서 19/26, 20/26을 맞힌다. 새 학습기는 이 기준과 비교해야 한다.
- 같은 `(model_id, variant, device)` 26개가 두 호스트에 모두 존재한다. 빠른 방식의 두 호스트 일치는 25/26이며, 불일치 1개는 `google/bert_uncased_L-8_H-512_A-8`, `sent-bs4-l64`, CPU다(64의 FL/SL 63,469/62,838 ms; 216의 54,801/59,669 ms). 두 호스트의 52쌍을 독립 앱 52개로 세면 안 된다.
- 주 평가는 **모델 ID 전체 제외 5 fold**로 양 호스트의 같은 모델을 함께 빼고, 각 fold의 짝 단위 정확도와 잘못 선택한 경우의 `wall_duration_ms` 초과량을 보고한다. 모델별 결과가 중요하다. 한 호스트에서 학습하고 다른 호스트에서 같은 26개 서술자를 평가하는 것은 **호스트 전이 민감도**일 뿐, 새로운 앱/모델 독립 시험이 아니다. 단순 규칙, 학습 그룹의 다수 방식, 장치별 완료시간 중앙값을 고정 비교군으로 둔다. 같은 그룹의 FL/SL을 서로 다른 train/test에 놓지 않는다.
- 모든 완전쌍은 2026-09-03 하루의 실행이다. runner가 방식 순서를 섞어 (`/path/to/aiops/federated_coordination_agent/live_scenario_runner.py:343`) 각 호스트에서 FL 먼저 12, SL 먼저 14쌍을 실행했다. 두 방식은 모든 쌍에서 순차 실행되므로 캐시 온도·호스트 혼잡·실행 순서가 효과에 섞인다. 첫 실행 방식이 더 빨랐던 그룹은 64에서 16/26, 216에서 17/26이다. 쌍의 차이를 FL 또는 SL의 인과효과라고 부르지 않는다.
- 모든 쌍의 `quality_metric_mean`은 NULL이고 실행 메트릭의 `quality_metric_valid`는 `false`다. “빠른 방식”은 학습 정확도·수렴 품질을 보장하지 않는다. 반복 수집이나 새 날짜가 없어 안정적인 채택 기준을 현재 데이터만으로 검증할 수 없다.

## 다른 후보가 더 직접적이지 않은 이유

`PARTITIONED` 분산 추론은 호스트마다 모델 10종 × 입력형상 4종 × CPU/GPU 2개 = 80개 성공 실행과 관측 `latency_p95_mean_ms`, `wall_duration_ms`를 갖는다. 다만 이 플랫폼에는 실측 `REPLICATED` 비교 방식이 없고, CPU/GPU의 배치 p95는 79/80쌍에서 GPU가 낮다. CPU/GPU “필요 여부” 학습 정답으로 만들면 거의 상수 분류가 되며, 각 모드의 p95는 세 참여자의 배치 p95 평균 (`/path/to/aiops/federated_coordination_agent/live_scenario_store.py:307`)이지 최종 사용자의 응답 p95가 아니다. 연속 완료시간/배치 지연 예측은 가능하지만 기존 KHU 정적 성능 예측과 연구 질문이 가깝다.

`fca-hf-volume-64` 두 shard에는 5,201개 제출, 성공 604개, `api_failed` 4,597개가 있다. 마지막 날짜 2026-09-05의 178개 제출은 성공 0개다. 이는 플랫폼 API 타임아웃·계획 거부가 주된 정답이다. 사전 사건만 쓴 시간순 제어면 건강 상태 진단의 자료가 될 수 있지만, 자원 호환성·VM 확장 효과를 판단하는 첫 지도학습 과제로 부적합하다. 두 shard는 같은 플랫폼에서 겹친 시간에 수집되어 독립 호스트 검증으로 취급할 수 없다. 기존 [원본 감사](local_signal_refresh_2026_09_28.md)의 실패 이유를 참고한다.

## 읽기 전용 재검산

아래 쿼리를 두 text-matrix DB 각각에서 `mode=ro`, `PRAGMA query_only=ON`으로 실행한다. `paired`는 같은 `group_id`의 두 실제 성공 실행만 허용한다.

```sql
WITH paired AS (
  SELECT g.group_id, g.model_id, g.variant, g.device, g.best_mode,
         MAX(CASE WHEN e.mode='FL' THEN e.wall_duration_ms END) AS fl_ms,
         MAX(CASE WHEN e.mode='SL' THEN e.wall_duration_ms END) AS sl_ms,
         MAX(CASE WHEN e.mode='FL' THEN e.submitted_at END) AS fl_at,
         MAX(CASE WHEN e.mode='SL' THEN e.submitted_at END) AS sl_at
  FROM scenario_groups g JOIN scenario_experiments e USING(group_id)
  WHERE g.task_type='federated_training'
    AND e.mode IN ('FL','SL') AND e.status='completed' AND e.success=1
  GROUP BY g.group_id
  HAVING COUNT(*)=2 AND COUNT(DISTINCT e.mode)=2
)
SELECT COUNT(*) AS paired_groups,
       COUNT(DISTINCT model_id) AS models,
       SUM(device='cpu') AS cpu_pairs,
       SUM(device='gpu') AS gpu_pairs,
       SUM(fl_ms<sl_ms) AS fl_faster,
       SUM(sl_ms<fl_ms) AS sl_faster,
       SUM(fl_at<sl_at) AS fl_submitted_first,
       SUM(best_mode=CASE WHEN fl_ms<sl_ms THEN 'FL' ELSE 'SL' END)
         AS stored_label_agrees
FROM paired;
```

이 감사는 새로운 채택 모델의 점수나 독립 앱 검증을 제공하지 않는다. 경희대가 해당 작업을 맡는다고 결정한다면, 위 범위의 사전 규약을 먼저 동결한 뒤에만 작은 모델·명시적인 보류 규칙을 시험한다.
