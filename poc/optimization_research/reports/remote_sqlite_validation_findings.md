# 보유 SQLite만으로 한 원격 모델 검증

## 검증에 쓸 수 있는 자료

`data/audit_v1/measurements.parquet`은 원본 `executions.sqlite3` 7개를 읽기 전용으로 추출한 고정 스냅샷이다. `khu-hf-local-exhaustive`는 `icns`의 RTX PRO 5000 Blackwell에서 9개 모델과 575개 요청 조건을 측정했다. 여기서 제외한 `khu-hf-remote-216`에는 `ubuntu216`의 RTX 5070 Ti에서 측정한 **새 모델 2개**(Pythia-160m, TinyLlama-1.1B)가 있다. 원격 캠페인의 1,085행 중 완료·성공·유효 타깃을 만족하는 행은 957개이고, 두 모델에 각각 12개 요청 조건이 있다. 원격 Qwen 5행은 모두 실패해 학습·검증 타깃이 아니다.

원격의 24개 요청 조건은 각각 GPU replica 1·2와 CPU core limit 2·4·8·16의 **8개 공통 자원 후보를 모두 포함**한다. 이 공통 영역은 192개 고유 `요청 조건×자원` 셀이며, 완료 회차의 중복을 포함하면 497행이다. 원격 전체는 CPU와 GPU replica 4 후보까지 포함해 344개 고유 셀이다. 로컬과 원격은 모델 ID와 요청 조건 그룹이 겹치지 않는다. 따라서 원격 자료를 학습에서 완전히 빼고 사후 스트레스 테스트를 할 수 있다.

다른 SQLite도 확인했다. `fca-hf-text-matrix-{64,216}`는 각 호스트에서 10개 encoder 모델×4개 입력 variant×CPU/GPU의 완료된 분산 추론 그룹 80개를 제공한다. 두 모델은 KHU exhaustive에 없지만, 이 자료는 `PARTITIONED` 플랫폼 실행으로 자원 후보가 CPU/GPU 두 종류뿐이고 기존 단일 프로세스 8후보 성능 측정과 직접 합칠 수 없다. `observed-lifecycle*`의 `dataset_runs.config_json`에는 Scale-out/in 결과가 **서로 다른 replica 수의 정적 측정쌍**이며 실제 실행한 라이프사이클 전환이 아니라고 명시되어 있다. UPDATE 관측도 없다. `khu-hf-live` 등 다른 KHU 캠페인은 같은 모델·호스트의 다른 입력 조건이며 collector v1/v2가 섞인 부분이 있다.

## 학습·평가 경계

평가 스크립트: [`remote_sparse_stress_test.py`](../scripts/remote_sparse_stress_test.py). 로컬 exhaustive 9개 모델의 GPU 8후보 결과로 절대 성능 모델과 anchor-conditioned 응답 모델을 학습했다. Pythia·TinyLlama의 성능 결과는 학습에 넣지 않았다. 각 원격 모델의 12개 입력 조건에서 모델 메타데이터와 입력 특징만 보고 medoid 방식으로 8개 기준 측정을 선택했다. 기준은 GPU replica 1·CPU core 2다. 모든 방법이 동일한 8개 측정을 이용하고, 모델마다 12개 입력 조건의 **기준 외 7개 후보 전체**(84셀)를 평가한다. MALE는 예측값과 측정값의 절대 로그오차 평균이다. 처리량은 `examples_per_second / batch_size`로 환산한 요청/초다.

원격에 정렬된 같은 작업의 다수 donor 모델이 없으므로 교차 모델 공분산 방법은 **smooth-kernel fallback**으로만 작동한다. `kernel_transfer`의 결과를 공분산 학습 효과로 해석하면 안 된다. 비교군은 zero-shot 절대 모델, 측정 오차의 전체 평균 이동, 직접 Ridge 보정, sparse conditional 응답, 12개 기준을 전부 측정한 conditional reference다.

| 방법 | 기준 측정 / 모델 | 평균 GPU-wall초 / 모델 | 지연 MALE | 요청/초 MALE |
| --- | ---: | ---: | ---: | ---: |
| Zero-shot | 0 | 0 | 0.327 | 0.299 |
| 전체 평균 보정 | 8 | 11.34 | **0.170** | 0.117 |
| 직접 Ridge 보정 | 8 | 11.34 | 0.194 | **0.089** |
| Sparse conditional | 8 | 11.34 | 0.178 | 0.108 |
| Smooth-kernel transfer | 8 | 11.34 | 0.178 | 0.121 |
| 전체 기준 측정 참고치 | 12 | 16.33 | 0.201 | 0.103 |

숫자는 새 모델 **2개**의 모델별 MALE를 동일 가중 평균한 값이다. K8의 평균 보정이 지연 오차에서, 직접 Ridge가 요청/초 오차에서 가장 낮다. Smooth kernel은 두 오차 어느 쪽에서도 강한 비교군을 넘지 못한다. 전체 기준 측정 참고치도 Pythia 지연에서는 일부 적은 측정 방법보다 나빠졌다. 기준 측정을 늘려도 로컬에서 학습한 자원 응답 함수의 원격 편향이 자동으로 사라지지 않는다는 신호다. K4 보조 실험에서는 sparse conditional이 지연 0.169, 요청/초 0.114였고 Ridge는 0.194/0.101이었다. 모델이 두 개뿐이라 이 차이로 새 방법의 우위를 주장할 수 없다.

## 중복 측정 민감도와 한계

주 분석은 완료·성공 측정 중 각 셀의 `started_at`, `execution_id` 순 첫 행을 고정 선택했다. 공통 8후보 영역의 497행에서 중복 305행을 제거해 192개 셀을 만들었다. 보조 분석은 셀별 지연·처리량·wall time의 중앙값을 사용했다. 이는 실제 한 회차에서 동시에 나온 값이 아닐 수 있어 민감도 확인에만 쓴다. K8 중앙값 결과는 평균 보정 0.172/0.118, Ridge 0.196/0.090, sparse conditional 0.180/0.108, smooth kernel 0.179/0.122다. 방법 순위와 결론은 첫 행 선택에 좌우되지 않았다.

원격은 모델, GPU, 호스트, 언어 모델 입력 분포가 **동시에** 바뀐다. 두 모델 모두 causal LM이며 로컬의 causal LM은 Qwen 하나뿐이다. 8개 probe는 원격 요청 조건 12개의 67%여서 희소 측정이라고 보기에도 제한적이다. `target_throughput_rps`와 `latency_slo_ms`는 수집기 코드의 프로필 값이지 독립적인 사용자 SLO가 아니다. 그러므로 이 실험은 예측 오차 스트레스 테스트이며, SLO 보장이나 실제 Scale-out/in 효과의 증거가 아니다.

재현 산출물: [K8 첫 회차](../artifacts/remote_sparse_stress_v1/metrics.json), [K8 중앙값](../artifacts/remote_sparse_stress_median_v1/metrics.json), [K4 첫 회차](../artifacts/remote_sparse_stress_k4_v1/metrics.json). 각 JSON은 원본 스냅샷·스크립트·의존 코드의 SHA-256, probe 그룹, 모델별 오차를 기록한다. 선택 규칙과 8후보 완전성 검사 2개를 통과했다.
