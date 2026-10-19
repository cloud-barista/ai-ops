# 기존 자료의 배포·스케일 판단 가능성과 다음 검증 과제

2026-09-28 현재의 읽기 전용 감사. 이 문서는 추가 학습 결과가 아니라, 현재 자료로 **새 과제를 시작할 수 있는지** 판단한 기록이다. 결론은 실제 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` **행동 효과 학습은 NO-GO**이고, 저장된 정적 성능 예측기의 **동일 작업·다른 SLO 재평가 비용을 줄이는 추론 최적화는 제한적 GO**다. 후자는 정확히 같은 예측·판단을 더 적은 CPU 시간에 계산하는 소프트웨어 실험이며, 새 형상의 안전성이나 새 알고리즘의 학술적 최초성을 입증하지 않는다.

## 자료 적격성 재검산

| 자료 | 확인한 수치와 의미 | 조치 결과 학습 판정 |
| --- | --- | --- |
| KHU 원본 `/home/researcher/aiops/runs/khu-hf-*/executions.sqlite3` 7개 | 읽기 전용 SQLite 스키마는 `collection_runs`, `execution_measurements`, `node_samples`뿐이다. 물리 실행 행은 캠페인별 `240, 210, 2,640, 4,856, 176, 384, 1,085`건으로 합계 **9,591건**이다. VM 생성·삭제, 배포 버전 변경, 행동 명령·완료와 사용자 요청 결과 테이블은 없다. | **NO-GO.** 형상별 별도 벤치마크는 명령 후 결과가 아니다. |
| KHU lifecycle ETL SQLite | `observed-lifecycle-local-space-filling`은 428 시나리오/1,120 결과, `remote-216-canonical-lifecycle`은 277/1,096이다. `SCALE_OUT/IN`은 서로 다른 정적 실행을 짝지어 만든 레이블이다. 대상 실행 뒤 GPU 사용률이 입력에 섞이고 `examples_per_second`를 요청/초로 취급한 오류가 있다. | **NO-GO.** 원본·파생 감사 (`untapped_signal_audit.md`)의 단위/누출 수정 전후와 관계없이 실제 scale 명령 정답은 생기지 않는다. |
| PAX Cassandra 실험 | 9개 폴더에서 온라인 노드 수 변화 58건(증가 30, 감소 28)이 있으나 명령 시각/ID가 없다. 이전·새 노드 수 표본 사이 간격은 **최소 185.00초**다. 사전 게이트의 120초 이내 적격 사건은 **0/58**이다. | **NO-GO.** 사전 전환 적격성 (`pax_transition_gate_findings.md`)은 결과 값을 보지 않고 중단했다. Cassandra 활성 노드도 KHU AI-App GPU VM이 아니다. |
| 다른 확보 자료 | SwissAI/BurstGPT/Azure는 요청 또는 지연·토큰 추적, GenTD26/Alibaba MaaS/Philly는 서비스·GPU 계측 또는 부하 추적이다. 이 저장소의 자료 계약 (`scale_effect_data_contract_v1.md`)에 필요한 동일 사건의 조치 ID·완료 시각·사용자 SLO 전후 결과가 없다. | **NO-GO.** 기존 부하·위험·정적 용량 연구의 추가 재분할을 독립 행동 검증으로 부르지 않는다. |

`poc/optimization_research/data/audit_v1/measurements.parquet`의 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 직접 읽어 재계수한 주 캠페인의 `eligible && success && device=cuda` 행은 **4,600건**, 고유 작업군 **575개**, 모델 **9개**다. 모든 작업군은 GPU replica 1/2개 × CPU affinity 2/4/8/16코어의 **완전한 8후보 묶음**이다. 각 작업군에는 원본 수집기가 생성한 SLO 쌍이 **한 개만** 기록돼 있다. 같은 작업에 서로 다른 실제 사용자 SLO 요청이 반복됐다는 증거는 없다.

## 제한적 GO: 동일 작업의 SLO 재평가에서 예측 재사용

저장 모델 (`../artifacts/performance_v1/performance_model.joblib`)의 SHA-256은 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`이다. 학습 특징 정의 (`../scripts/train_performance.py`)에는 모델·revision·batch/length/concurrency·replica/CPU affinity·반복 수가 있지만 `latency_slo_ms`와 `target_throughput_rps`는 없다. 지원 범위 검사 (`../scripts/predict_performance.py`) 역시 SLO를 보지 않는다. 따라서 **모델 아티팩트, 동일 호스트/장치와 완전히 같은 8후보가 고정**되면, 후보별 배치 p95와 examples/s 예측을 한 번만 계산한 뒤 다른 SLO 쌍의 적합 여부를 재평가해도 원래 함수를 매번 호출한 결과와 같아야 한다. 요청/초는 매번 `examples_per_second / batch_size`로 변환한다.

이는 이미 구현된 8후보 일괄 채점 (`performance_batch_predict_findings.md`)과 **다른 반복 축**이다. 그 개선은 한 SLO 질의의 8후보를 한 모델 호출로 묶었다. 이번 후보는 같은 후보 묶음에 대해 **SLO만 달라진 여러 질의**에서 예측을 재사용한다. 기존 2단계 추론 실패 보고서 (`cascade_inference_findings.md`)는 이 재사용 가능성을 이미 언급했다. 따라서 신규 발명이나 학술적 고유 방법이라고 주장하지 않는다. 현재는 오프라인 형상 평가기 (`../scripts/assess_gpu_shape.py`)의 연구용 CPU 경로에만 적용할 수 있으며, 운영 캐시 적중률은 자료에 없다.

### 실행 전 고정할 평가 규약

1. **입력과 분할:** 위 해시의 원본·저장 모델을 바꾸지 않는다. 575개 완전 작업군에서 `SHA256(workload_group)` 순으로 모델별 처음 5개씩, 총 45개를 선택한다. 각 작업군의 원본 생성 목표 `(L₀, R₀)`에 대해 지연 상한 `L₀ × {0.50, 0.75, 1.00, 1.25, 1.50}`과 요청/초 하한 `R₀ × {0.50, 0.75, 1.00, 1.25, 1.50}`의 곱집합인 **25개 합성 SLO 쌍**을 동일 8후보에 적용한다. `L₀, R₀`의 전체 최소값은 각각 346ms·12요청/초로 양수다. 이 25개는 성능 정답이 아닌 추론 호출 패턴이며 사용자 요청으로 세지 않는다. 선택한 ID를 점수 실행 전에 별도 manifest에 고정한다.
2. **비교:** A는 각 SLO에서 기존 8후보 `predict_records` 1회와 형상 선정을 다시 수행한다. B는 같은 묶음을 첫 SLO에서 한 번 채점하고 이후 24개 SLO에서는 숫자 예측을 재사용해 같은 형상 선정식을 실행한다. 둘 다 저장 모델 로드와 네트워크 I/O는 타이머 밖에 두되 후보 검증, 지원 검사, 요청별 SLO 평가와 결과 구성은 동일 범위로 잰다. 첫 질의의 cold path와 두 번째 이후 warm path를 따로 보고한다.
3. **정확성·안전 불변식:** 45×25 질의에서 후보별 상태, `ABSTAIN` 이유, 예측값, GPU→CPU 최소 선택, 요청/초 단위, 결과 순서가 완전 동일해야 한다. unsupported 후보, 변경된 모델 SHA, `model_revision`, batch/length/concurrency, host/device, CPU/GPU 형상, 후보 순서 변경 시에는 재사용하지 않는 테스트를 둔다. 키는 SLO를 제외하되 **예측과 지원 검사에 관여하는 모든 후보 필드·모델 SHA**를 포함한다. 요청별 `request_id` 같은 메타데이터는 캐시하지 않는다. 캐시 실패는 기존 경로로 되돌린다.
4. **시간 측정과 채택 기준:** 모델을 예열하고 경로 순서를 고정 난수로 섞어 45개 묶음을 각각 5회 반복한다. 묶음당 25질의 전체 wall time과 warm 질의 p50/p95를 기록한다. 동일 결과가 필수이며, 25질의 묶음 중앙 시간이 A 대비 **3배 이상** 줄고 B의 첫 질의 p95가 A보다 **20% 이상 늘지 않을 때만** 연구용 최적화로 채택한다. 캐시 엔트리의 직렬화 크기와 hit/miss 수도 기록한다. 분산 서버, JSON/API, VM 실행 시간 개선을 주장하지 않는다.
5. **해석 경계:** 같은 작업군의 25개 SLO는 합성 반복이다. 실제 트래픽에서 SLO 변경 재질의가 얼마나 자주 발생하는지는 이 SQLite로 추정할 수 없다. 동등성이 통과해도 배포 승인 안전성·실제 scale 효과는 증명되지 않는다. 사전 동결된 독립 모델/호스트 시험이 없으므로 관련 오류율 개선도 주장하지 않는다.

**우선순위:** 이 최적화는 실제 조치 결과를 학습시키는 연구를 대신하지 않는다. 빠른 오프라인 평가기의 재현성 개선으로는 실행 가능하다. 행동 연구를 다시 열 조건은 데이터 계약 (`scale_effect_data_contract_v1.md`)의 같은 `decision_id → action_id → ready 시각 → 사용자 결과` 연결, 비교 가능한 부하/형상, 실제 사용자 SLO가 확보되는 것이다. 그 전에는 현재 학습기를 `SCALE_OUT/IN` 또는 `UPDATE` 정답 모델로 훈련하지 않는다.

### 재검산 명령

아래 명령은 원본을 읽기 전용으로 열며 위 완전 작업군 수와 원본 SLO 반복 여부만 재계수한다.

```bash
cd /home/researcher/ai-mcmp
/usr/bin/python3 - <<'PY'
import pandas as pd
x = pd.read_parquet('poc/optimization_research/data/audit_v1/measurements.parquet')
x = x[(x.campaign == 'khu-hf-local-exhaustive') & (x.device == 'cuda') & x.eligible & x.success]
shapes = {(g, c) for g in (1, 2) for c in (2, 4, 8, 16)}
groups = list(x.groupby('workload_group'))
assert len(x) == 4600 and len(groups) == 575
assert all(len(part) == 8 and set(zip(part.replica_count, part.cpu_core_limit)) == shapes
           for _, part in groups)
assert all(part[['latency_slo_ms', 'target_throughput_rps']].drop_duplicates().shape[0] == 1
           for _, part in groups)
print('4600 rows; 575 complete 8-shape workloads; 1 generated SLO pair per workload')
PY
```
