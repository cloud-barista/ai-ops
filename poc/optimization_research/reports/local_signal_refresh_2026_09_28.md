# 로컬 SQLite 신호 재점검 (2026-09-28)

## 결정

현재 로컬 SQLite에는 기존 감사 뒤 새로 쌓인 KHU 물리 측정이나 실제 `UPDATE`·`SCALE_OUT`·`SCALE_IN` 실행 결과가 없다. 요구사항→CPU/RAM/GPU/노드 수 정답, 사용자 SLO, VM 생성·삭제 시각도 새로 발견되지 않았다. 따라서 이 DB로 새 동적 배포·스케일 정책을 지도학습하지 않는다. 기존 정적 형상별 성능·관측 메모리·반복 변동 실험은 계속 활용할 수 있지만, 새 독립 검증 자료로 취급하지 않는다.

## 확인 범위와 수치

읽기 전용 SQLite URI(`mode=ro`, `PRAGMA query_only=ON`)로 `/path/to/aiops/runs`의 DB를 조회하고, `/home/researcher/ai-mcmp` 아래의 `.db`·`.sqlite`·`.sqlite3`·`.db3` 파일을 검색했다. 후자에는 DB 파일이 없다. [기존 추출 감사](../data/audit_v1/data_audit.md)의 2026-09-28 02:14 UTC 스냅샷과 원본 7개의 `execution_measurements`·`collection_runs` 수가 모두 일치한다.

| 원본 `/path/to/aiops/runs/.../executions.sqlite3` | 측정 행 | 마지막 `execution_measurements.finished_at` (UTC) |
| --- | ---: | --- |
| `khu-hf-cpu-scaling-local` | 240 | 2026-09-11 06:18:02 |
| `khu-hf-diverse-cpu-matrix` | 210 | 2026-09-11 09:01:45 |
| `khu-hf-live` | 2,640 | 2026-09-11 08:42:48 |
| `khu-hf-local-exhaustive` | 4,856 | 2026-09-11 16:53:49 |
| `khu-hf-local-novel-matrix` | 176 | 2026-09-11 09:21:19 |
| `khu-hf-local-space-filling` | 384 | 2026-09-11 09:44:55 |
| `khu-hf-remote-216` | 1,085 | 2026-09-11 09:39:01 |

합계 9,591행, 기존 기준에 맞는 학습 후보 9,445행이다. 세 물리 테이블은 `collection_runs`, `execution_measurements`, `node_samples`뿐이다. `target_throughput_rps`와 `latency_slo_ms`는 수집기 설정의 목표이고 사용자 요청 정답이 아니다. `examples_per_second`는 예제/초이며 서비스 요청/초를 직접 측정하지 않는다. [원본·파생 구분과 라벨 오류](untapped_signal_audit.md)

## 그럴듯해 보이는 추가 DB의 라벨 의미

- `/path/to/aiops/runs/khu-live-placement/*.sqlite3` 및 `khu-hf-remote-216/{placement,observed-lifecycle}.sqlite3`의 `action_outcomes`는 위 실행 DB를 읽은 ETL이다. `DEPLOY`는 정적 실행 결과의 재포장이다. `SCALE_OUT`·`SCALE_IN`은 별개 복제 수에서 측정한 정적 행을 짝지은 값이다. 예를 들어 `observed-lifecycle-local-space-filling.sqlite3`의 1,120개 결과는 `DEPLOY` 384, `KEEP` 384, `SCALE_OUT` 176, `SCALE_IN` 176개이고, `dataset_runs.config_json.transition_evidence`는 `paired_static_measurements_not_executed_lifecycle_transitions`라고 명시한다. `UPDATE` 관측은 없다. `observed-lifecycle-cpu-scaling-local-20260911.sqlite3`의 `SCALE_OUT` 표본에서는 `feature_json.candidate_gpu_utilization`이 같은 행 `outcome_json.measured.gpu_utilization_percent`와 동일해 사후 후보 계측 누출도 남아 있다.
- `/path/to/aiops/runs/khu-deployment-scenarios/scenarios.{incomplete,pre-feasibility-fix}-seed42.sqlite3`는 각각 `deployment_scenarios` 5,000개, `action_outcomes` 40,000개다. 각 DB에 `UPDATE` 결과가 12,000개 있지만 `dataset_runs.config_json.generator_version`은 `khu-deployment-scenario-generator/v1`, `source_db`는 FCA 합성 시나리오 DB다. 실측 업데이트 라벨이 아니다. 출처 FCA DB의 `scenarios.label_source` 1,000개는 모두 `fca-heuristic/v0.5`다.
- FCA 실험 DB에는 별도의 **실행 결과**가 있다. 예를 들어 `fca-hf-text-matrix-64/executions.sqlite3`의 `scenario_experiments`는 154행(FL 48, SL 26, PARTITIONED 80)이며 2026-09-03에 전부 성공했다. `scenario_groups`는 128개다. `fca-hf-volume-64` 두 shard의 `scenario_experiments`는 합계 5,201행(2026-09-03~05), 성공 604행, `api_failed` 4,597행이다. `scenario_groups.best_mode`는 실제 성공한 FL/SL/PARTITIONED 중 **완료 시간이 가장 짧은 모드**로 계산하며, 모든 모드 실패 시 비어 있다(구현 (`/path/to/aiops/federated_coordination_agent/live_scenario_store.py:428`)). 실패에는 API 타임아웃과 분산 계획 거부가 포함된다. 이는 연합 학습·분산 추론 실행 **모드** 비교 자료다. 좁게는 모드별 성공/완료시간 예측을 탐색할 수 있지만, AI-App VM 필요 사양·배포 업데이트·복제 수 전환·사용자 SLO의 정답은 아니다. 대량 API 실패가 있는 volume shard를 성능 용량 학습에 바로 합치지 않는다.

### FCA 실행 기록의 마지막 학습 후보 재점검 (2026-09-29)

`/path/to/aiops/runs/` 아래 네 DB를 다시 `mode=ro`, `PRAGMA query_only=ON`으로 확인했다. 표의 CPU/GPU 쌍은 **같은 호스트 DB에서 `model_id`와 `variant`가 같은 별도 시나리오 두 행**이라는 뜻이다. 같은 실행을 자원만 바꿔 재시도했다는 인과적 짝은 아니다.

| DB (`.../executions.sqlite3`) | `scenario_experiments` / 성공 | 분산 추론의 `PARTITIONED` / 성공 | 같은 `model_id`·`variant`의 CPU/GPU 서술자 쌍 |
| --- | ---: | ---: | ---: |
| `fca-hf-text-matrix-64` | 154 / 154 | 80 / 80 | 40 / 40 |
| `fca-hf-text-matrix-216` | 154 / 154 | 80 / 80 | 40 / 40 |
| `fca-hf-volume-64/shard-0` | 2,766 / 328 | 1,344 / 197 | 성공 행만으로 완전 쌍 0 |
| `fca-hf-volume-64/shard-1` | 2,435 / 276 | 1,347 / 182 | 성공 행만으로 완전 쌍 0 |

두 text-matrix DB의 분산 추론에는 `PARTITIONED`만 있고 비교할 `REPLICATED` 실행이 없다. 각 DB의 40개 CPU/GPU 서술자 쌍은 모델 10종의 한 날짜 기록이다. 두 호스트의 `latency_p95_mean_ms`를 서술자별로 비교하면 GPU 쪽 값이 80쌍 중 79쌍에서 작지만, 장비·실행 환경·시나리오 컨텍스트가 함께 달라진 관측값이므로 GPU 배치의 인과 효과나 새 AI-App의 요구 GPU 정답으로 쓰지 않는다. `FL`·`SL` 비교는 **연합 학습** 작업에만 있다.

두 volume shard의 실패 4,597건은 모두 `status='api_failed'`다. `error_json.message`의 문자열을 읽으면 시간 초과 **4,103**, `ORCHESTRATOR_AGENT_REJECTED` **493**, `JOB_CONFLICT` **1**건이다. 마지막 날짜인 **2026-09-05에는 178번 제출해 성공 0건**이다. 이 실패 분포로 지도학습하면 추론의 자원 적합성보다 당시 API·계획기 상태를 주로 맞힐 위험이 크다. `scenario_groups`의 `task_type/model_id/variant/device/context_json`과 `scenario_experiments`의 `mode/success/status/submitted_at/latency_p95_mean_ms/error_json`은 실행 실험의 기술에는 충분하지만 VM 생성 명령·준비 완료·요청별 사용자 SLO를 잇는 열은 아니다. 이 후보의 모델 채점이나 보류 시험 열람은 수행하지 않았다.

재검산 SQL은 각 DB를 읽기 전용으로 연 뒤 실행한다. 마지막 쿼리는 두 volume shard에만 적용하며, 합계는 shard별 결과를 더한다.

```sql
SELECT g.task_type, e.mode, COUNT(*) AS n, SUM(e.success) AS successes
FROM scenario_experiments e JOIN scenario_groups g USING (group_id)
GROUP BY g.task_type, e.mode;

SELECT COUNT(*) AS descriptor_pairs
FROM (
  SELECT g.model_id, g.variant
  FROM scenario_experiments e JOIN scenario_groups g USING (group_id)
  WHERE g.task_type='distributed_inference' AND e.mode='PARTITIONED' AND e.success=1
  GROUP BY g.model_id, g.variant
  HAVING COUNT(*)=2 AND COUNT(DISTINCT g.device)=2
);

WITH matched AS (
  SELECT g.model_id, g.variant, COUNT(*) AS n,
         MAX(CASE WHEN g.device='cpu' THEN e.latency_p95_mean_ms END) AS cpu_p95,
         MAX(CASE WHEN g.device='gpu' THEN e.latency_p95_mean_ms END) AS gpu_p95
  FROM scenario_experiments e JOIN scenario_groups g USING (group_id)
  WHERE g.task_type='distributed_inference' AND e.mode='PARTITIONED' AND e.success=1
  GROUP BY g.model_id, g.variant
)
SELECT COUNT(*) AS descriptors, SUM(n=2 AND gpu_p95<cpu_p95) AS gpu_lower_p95
FROM matched;

SELECT substr(submitted_at,1,10) AS utc_day, COUNT(*) AS n, SUM(success) AS successes
FROM scenario_experiments GROUP BY utc_day ORDER BY utc_day;

SELECT CASE
  WHEN json_extract(error_json,'$.message') LIKE '%timed out%' THEN 'timeout'
  WHEN json_extract(error_json,'$.message') LIKE '%ORCHESTRATOR_AGENT_REJECTED%' THEN 'planner_reject'
  WHEN json_extract(error_json,'$.message') LIKE '%JOB_CONFLICT%' THEN 'job_conflict'
  ELSE 'other' END AS reason, COUNT(*) AS n
FROM scenario_experiments WHERE success=0 GROUP BY reason;
```

## 다음 데이터 조건

동적 판단을 학습·검증하려면 실제 명령의 입력·발행시각, 이전/이후 VM 형상, 배포·VM 완료시각, 장애·롤백, 사용자 부하와 SLO를 같은 app/버전/시각 키로 연결해 기록해야 한다. 현재 SQLite에는 이 결합 라벨이 없으므로 공개 트레이스도 개별 관측 대상의 범위에 맞춰 별도로 검증한다.
