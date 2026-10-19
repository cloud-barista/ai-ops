# GPU 1개 계측 후 GPU 2개 프로파일의 소요시간을 예측할 수 있는가

2026-09-29, **읽기 전용 적격성 점검·사후 탐색**. 경희대의 형상 추천 및 배포·제어 추론 최적화에서 *다음 형상 프로파일에 필요한 시간 예산*을 추정하는 별도 목표다. 사용자 요청 지연, VM 준비시간, `SCALE_OUT` 효과 또는 실제 GPU 사용료의 정답으로 해석하지 않는다. **결론: 학습·검증은 가능하지만 현재 우선 연구 방법으로 추천하지 않는다.** 매우 강한 단순 기준선이 이미 근접하므로, 별도 학습기는 아래 사전 중단 게이트를 통과할 때에만 의미가 있다.

## 왜 기존 목표의 산술적 재표현이 아닌가

원본 `khu-hf-local-exhaustive/executions.sqlite3`의 `execution_measurements`는 CUDA 1개 2,300행, CUDA 2개 2,300행, CPU 256행이다. 분석 적격 CUDA 행을 `(model_id, model_revision, workload_group, cpu_core_limit)`로 묶으면 GPU 1→2의 **고유 2,300쌍**이 생기며, 모든 쌍에서 GPU 1개 결과의 `finished_at`이 GPU 2개 실행 `started_at`보다 빠르다. 고정 추출 (`../data/audit_v1/measurements.parquet`)의 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 주 캠페인은 encoder 8모델의 2,048쌍과 causal LM Qwen 1모델의 252쌍이다.

정답 `E₂`는 GPU 2개 실행 행의 `finished_at - started_at`(초)다. 이는 수집기가 프로파일을 실행한 **로컬 제어 구간**이다. 수집기 실행 루프 (`/path/to/aiops/khu_policy_agent/live_collector.py:1418`)는 시작시각을 실행 직전에 기록하고, 2개 GPU에서는 별도 자식 프로세스를 spawn·join (`/path/to/aiops/khu_policy_agent/live_collector.py:1606`)한다. DB 종료시각은 집계 결과의 `finished_at` (`/path/to/aiops/khu_policy_agent/live_collector.py:1117`)이다. DB 기록·샘플러 종료 뒤의 시간은 포함하지 않는다. `wall_seconds`는 자식별 내부 시간의 최댓값 (`/path/to/aiops/khu_policy_agent/live_collector.py:1663`)이며, `examples_per_second`는 처리 예제 수/`compute_seconds` (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py:665`)다. 두 GPU 실행은 각 자식이 같은 전체 작업을 하므로 `examples`가 두 배가 되지만, `E₂`는 처리량이나 지연의 산술적 변환이 아니다.

실측 전체 중앙값은 GPU 1개의 `E₁=0.662초`, GPU 2개의 `E₂=4.609초`다. `E₂`에서 기록된 자식 `wall_seconds`를 뺀 제어 오버헤드는 중앙 `2.655초`(5–95분위 `2.558–3.030초`)이고, 단일 GPU는 중앙 `0.054초`다. GPU 2개 프로파일의 `wall_seconds` 자체도 중앙 `1.956초`로 GPU 1개의 `0.601초`보다 길다. 이는 프로세스 시작·로드·자원 경쟁을 포함한 실제 차이다. 그러나 **GPU 1개 시간에 일정 오버헤드를 더하는 기준선이 이미 매우 정확**해서 복잡한 모델의 실용적 여지가 작다.

## 누수 없는 학습 질문과 시간·모델 분할

판단 시점은 대상 작업의 GPU 1개 측정 종료 직후, GPU 2개 프로파일 시작 전이다. 허용 입력은 그 GPU 1개 실행의 `E₁`, `wall_seconds`, `load_seconds`, `compute_seconds`, 실행 전 batch·길이·동시성·반복·CPU affinity, GPU 1개 결과에서 이미 확인한 파라미터 바이트다. 등록 전 예측이 아니라 **첫 GPU 측정 뒤** 예측이므로 이 관측을 사용할 수 있다. 대상 GPU 2개의 `result_json`, `node_summary_json`, 부하·처리량·완료시각이나 이후 환경값, `profile_name`/모델 ID는 입력하지 않는다. 파라미터 바이트는 GPU 1개 실행의 결과에서만 읽고 GPU 2개 결과의 복제 합산 바이트를 쓰지 않는다.

시간 절단은 **2026-09-11 13:00:00 UTC**다. `GPU2.finished_at ≤ 절단`인 1,044쌍만 훈련한다. `GPU1.started_at ≥ 절단`인 1,256쌍만 시험한다. 경계에 걸친 쌍은 제외한다. 시험 중 encoder는 8모델·1,004쌍(모델당 124–128쌍), Qwen은 252쌍이다. 각 바깥 분할에서 대상 encoder 모델의 **절단 이전 행도 모두 훈련에서 제외**하고, 나머지 7모델의 절단 이전 912–916쌍만 적합한다. 이로써 모델 제외와 예측 시각 이전의 라벨 가용성을 동시에 만족한다. Qwen은 절단 이전 같은 작업 종류 donor가 0모델이어서 수치 예측을 `ABSTAIN`한다. 13시 절단과 이 목표는 이미 이 SQLite의 다른 결과를 많이 본 뒤 정했으므로 이 시험을 독립 확인이라고 부르지 않는다. `novel-matrix`·`space-filling`은 주 캠페인보다 **먼저** 수집되어 시간상 미래 시험으로 쓰지 않는다.

강한 기준선은 대상 GPU 1개 실행의 `E₁`에 **다른 encoder의 절단 이전** `E₂-E₁` 중앙값 또는 90분위를 더하는 식이다. 곱셈형 `E₁×median(E₂/E₁)`과 정적 특징 Ridge도 함께 비교한다. 학습 후보는 `E₂-E₁`의 90분위를 위 입력으로 추정하는 고정 작은 히스토그램 부스팅 회귀기 한 개다. 평균시간 예측도 별도 점수화하려면 같은 입력의 평균 회귀기 한 개를 고정한다. 훈련 대상과 하이퍼파라미터, 결측 처리, `E₂>0` 강제는 **후보 학습·후보 점수 전에** 잠가야 한다. 90분위의 강한 비교 상대는 `E₁+donor p90(E₂-E₁)` 및 Ridge 예측+훈련 잔차 p90이다. 모델마다 같은 가중치를 주고, 초 단위 pinball 손실·90% 피복률·과대 예산폭과 5초 예산 아래 잘못된 가능 판정을 함께 보고한다. 단순 기준선으로 시험 정답 일부를 이미 열었으므로 이 절차를 독립 사전 등록 평가로 부를 수 없다.

## 측정 가능한 의사결정 가치와 조기 중단

사후 단순 기준선 예비 계산에서, 13시 이후 encoder 1,004쌍 중 `E₂≤5초`는 **783쌍**이다. donor의 절단 이전 오버헤드 중앙값을 `E₁`에 더하면 770건을 5초 이내로 판단해 750건이 맞고 **20건은 예산을 초과**한다. donor p90을 더하면 720건을 가능으로 판단해 716건이 맞고 **4건은 예산을 초과**한다. p90 기준선의 모델 동일 가중 pinball 손실은 **0.0204초**, 경험적 피복률은 **89.1%**다. 중앙값 기준선의 모델 동일 가중 평균 절대 로그오차는 **0.0181**이다. 5초는 실제 서비스 SLO가 아니라 이 소요시간 분포를 점검하기 위한 예시 예산이며, 앞서 자료를 보았으므로 성과 주장용으로 사용하지 않는다. 모델별 5초 가능한 비율은 서로 달라 예산 판정 자체는 비상수가 맞지만, 학습 여지는 작다.

**점수 전 자료 게이트:** 2,300쌍의 키가 유일하고 GPU 1개가 항상 먼저 끝나며 양쪽 시간과 첫 실행 입력이 양수·유한해야 한다. 각 바깥 encoder 모델의 다른 모델 사전 훈련이 5모델·500쌍 이상, 사후 대상 시험이 100쌍 이상이어야 한다. 한 모델의 반복·형상·조건은 독립 앱 수로 세지 않는다. 동일 모델이 다른 캠페인 또는 `khu-hf-live` 반복 자료를 통해 훈련에 섞이면 중단한다. 예비 집계로는 주 캠페인 8개 encoder 분할이 이 건수 조건을 충족한다.

**후보 채택 게이트:** 고정 학습기가 두 강한 p90 기준선 중 더 낮은 모델 동일 가중 pinball 손실보다 **상대 5% 이상 낮고**, 총 피복률 **88% 이상**, 8모델 중 **6모델 이상에서** 더 낮은 손실, 어떤 모델에서도 더 강한 기준선보다 **20% 초과 손실 악화 없음**, 5초 예시 예산에서 잘못된 가능 판정 **4건 이하**를 모두 충족해야 한다. 못 맞추면 추가 특징·문턱을 시험값에 맞춰 조정하지 않고 학습기를 미채택한다. 통과해도 한 호스트·한 수집기·이미 본 모델 계열의 **프로파일링 예산 예측**일 뿐, VM 생성시간·실제 자원비 절약·학술적 신규성 또는 배포 제어 효과로 승격하지 않는다.

## 재현할 최소 읽기 전용 점검

아래 코드는 고정 추출에서 예비 건수와 핵심 라벨을 확인한다. 동일 행 수는 원본 SQLite를 `file:/path/to/aiops/runs/khu-hf-local-exhaustive/executions.sqlite3?mode=ro`로 열어 `execution_measurements`의 `device,replica_count`별 `count(*)`와 비교한다.

```python
import pandas as pd

d = pd.read_parquet('data/audit_v1/measurements.parquet')
d = d[(d.eligible) & (d.host == 'icns') &
      (d.campaign == 'khu-hf-local-exhaustive') & (d.device == 'cuda')].copy()
d['elapsed'] = (pd.to_datetime(d.finished_at) - pd.to_datetime(d.started_at)).dt.total_seconds()
k = ['model_id', 'model_revision', 'workload_group', 'cpu_core_limit']
assert not d.duplicated(k + ['replica_count']).any()
p = d.pivot_table(index=k, columns='replica_count',
                  values=['elapsed', 'wall_seconds', 'started_at', 'finished_at'],
                  aggfunc='first').dropna()
assert len(p) == 2300
assert (pd.to_datetime(p['finished_at'][1]) < pd.to_datetime(p['started_at'][2])).all()
cut = pd.Timestamp('2026-09-11T13:00:00Z')
before = p[pd.to_datetime(p['finished_at'][2]) <= cut]
after = p[pd.to_datetime(p['started_at'][1]) >= cut]
assert (len(before), len(after)) == (1044, 1256)
encoder = set(before.index.get_level_values('model_id'))
assert len(encoder) == 8
counts = dict(n=0, feasible=0, median_approved=0, median_false=0,
              p90_approved=0, p90_false=0)
for model in sorted(encoder):
    donor = before[before.index.get_level_values('model_id') != model]
    target = after[after.index.get_level_values('model_id') == model]
    delta = donor['elapsed'][2] - donor['elapsed'][1]
    truth = target['elapsed'][2]
    counts['n'] += len(target)
    counts['feasible'] += int((truth <= 5).sum())
    for prefix, q in [('median', 0.5), ('p90', 0.9)]:
        approved = target['elapsed'][1] + delta.quantile(q) <= 5
        counts[prefix + '_approved'] += int(approved.sum())
        counts[prefix + '_false'] += int((approved & (truth > 5)).sum())
assert counts == dict(n=1004, feasible=783, median_approved=770,
                      median_false=20, p90_approved=720, p90_false=4)
```

기존 연구의 [GPU 복제 총 CPU초](replica_cpu_work_findings_v1.md)는 **프로세스 CPU 작업량**, [배치 성능](../artifacts/performance_v1/experiment_report.md)은 지연·추론 구간 처리량, [반복분포](repeated_measurement_distribution_findings.md)는 동일 형상의 미래 성능 변동을 목표로 한다. 여기의 `E₂`는 GPU 2개 *측정을 실행하기 전에 필요한 시간 예산*이라는 다른 라벨이다. 다만 이미 정확한 덧셈 기준선 때문에 현재 자료에서는 새로운 고유 방법보다 **단순 예산 규칙의 문서화**가 우선이다.
