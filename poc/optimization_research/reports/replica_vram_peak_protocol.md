# 현재 GPU 1개 측정으로 GPU 2개 실행의 장치별 예약 피크 추정

2026-09-29. **모델 점수 계산 전에 고정한 탐색 규약.** 목표는 같은 `icns` 호스트에서 동일한 배치 추론 조건을 GPU 1개로 측정한 뒤, GPU 2개를 각각 별도 프로세스로 실행했을 때의 **장치별 PyTorch 예약 메모리 피크 중 큰 값**의 90% 조건부 상한이다. 이것은 관측된 allocator 예약량이지 최소 VRAM, OOM 위험, VM 메모리 요구량 또는 실제 Scale-out 결과가 아니다. 원본 SQLite와 추출 파일은 이미 여러 연구에서 살펴봤고 이번 라벨의 기초 분포도 점검했다. 따라서 이후 점수는 독립 확인이 아닌 탐색 결과다.

## 고정 입력·대상·지원 범위

- 동결 추출 `data/audit_v1/measurements.parquet`의 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 원본은 `/path/to/aiops/runs/khu-hf-{local-exhaustive,local-novel-matrix,local-space-filling}/executions.sqlite3`의 물리 실행이다. 모두 `eligible`, `device=cuda`, `success=1`인 행만 사용한다. 주 캠페인은 575개 작업 조건 × CPU affinity 2/4/8/16코어 = **2,300개 GPU1→GPU2 짝**이다. 별도 입력 조건 캠페인은 각각 72·176짝이다.
- 정확한 짝 키는 `(campaign, host, model_id, model_revision, workload_group, cpu_core_limit)`이다. 각 짝에는 `replica_count=1`과 `2`가 한 행씩 있어야 하고, 입력 batch·길이·동시성·반복 수·dataset과 CPU thread/core 값이 같아야 한다. GPU 인덱스는 `[0]`→`[0,1]`, GPU1 종료가 GPU2 시작보다 빨라야 한다. 중복·누락·순서 위반을 조용히 보정하지 않고 종료한다.
- 입력은 GPU1 실행 **종료 후** 알 수 있는 `gpu_memory_reserved_bytes`, `result_json.model_parameter_bytes`, 그리고 요청·실험 프로필의 batch 크기, 최대 길이, 동시성, 반복 수, CPU core limit뿐이다. 대상 GPU2 행의 `result_json`, GPU2 메모리, 실행 중/후 node telemetry, 지연·처리량, 생성 SLO는 특징에 들어가지 않는다. GPU1 사용량은 이미 측정한 현재 응용 신호이며 순수 등록 시점 추천 입력이 아니다.
- 정답은 GPU2 `result_json.replica_results`의 서로 다른 물리 GPU 0·1에 기록된 `gpu_memory_reserved_bytes` 두 값의 **최댓값**이다. 결과 목록의 순서는 보장되지 않으므로 GPU 인덱스 집합 `{0,1}`을 검사하고 목록 순서에 의존하지 않는다. 상위 `execution_measurements.gpu_memory_reserved_bytes`는 두 프로세스 피크의 합이므로 장치 용량과 직접 비교하지 않는다. 원본 합계와 각 결과의 합이 일치하는지 검증한다. 주 캠페인 2,300짝 중 39짝은 두 GPU 피크가 달라 합계의 절반도 정답이 아니다.
- 주 시험은 encoder 추론 **8개 모델**을 하나씩 통째로 제외한다. Qwen은 로컬에서 유일한 causal LM이고 동종 donor가 없으므로 학습기 출력은 `ABSTAIN`한다. Qwen 252짝의 GPU1 유지 기준선 성적은 지원 실패 진단으로만 따로 기록한다. `model_id`를 학습 특징으로 사용하지 않는다.

## 고정 예측과 분할

세 방법은 각 바깥 fold에서 동일한 **나머지 7개 encoder 모델**만 학습에 쓴다. 대상 모델의 GPU1 피크는 세 방법에 동일하게 제공하고, 대상 GPU2 결과는 점수 계산에만 쓴다. CPU core 4개와 같은 작업 조건의 행은 함께 대상 모델 fold에 들어간다. 평균을 먼저 작업 조건 내 4코어로, 이어서 모델별로 낸다. 2,048개 행을 서로 독립인 AI-App 2,048개로 세지 않는다.

1. `unchanged_gpu1`: 장치당 GPU1 피크를 그대로 GPU2 장치별 상한으로 사용한다.
2. `donor_q90_delta`: donor의 `(GPU2 최대 피크 − GPU1 피크)` MiB를 모아 NumPy `quantile(0.9, method="higher")` 한 값만 학습한다. GPU1 피크에 더한다.
3. `hgb_q90_delta`: 같은 MiB 차이를 90% 분위수 HGB 한 개로 학습한다. 특징은 `log1p(GPU1 피크 MiB)`, `log1p(GPU1 모델 파라미터 MiB)`, `log(batch_size)`, `log(max_length)`, `log1p(request_concurrency)`, `log(repeats)`, `log(cpu_core_limit)`이다. HGB 설정은 `loss="quantile"`, `quantile=0.9`, `max_iter=80`, `max_leaf_nodes=8`, `min_samples_leaf=40`, `l2_regularization=10`, `learning_rate=0.05`, `early_stopping=False`, `random_state=42`로 고정한다. 세 방법 모두 결과 상한을 최소 1 MiB로 자른다. 하이퍼파라미터나 보류 문턱을 시험 점수로 재조정하지 않는다.

원본 주 캠페인의 바깥 **leave-one-model-out** 결과가 주 평가다. 같은 모델의 `novel-matrix`와 `space-filling` 행에는 주 캠페인의 해당 모델을 제외한 donor 학습기를 그대로 적용하고 외부 행으로 재학습·선택하지 않는다. 두 캠페인의 조건은 이미 다른 연구에서 여러 번 확인했으며 신규 독립 검증이 아니다. 다른 호스트·GPU·모델군이 동시에 달라지는 `remote-216`은 이 방법의 지원 범위 밖이라 모델 점수를 내지 않는다.

## 점수·보류 기준

기본 손실은 `q=0.9` pinball loss(MiB)이며 작을수록 좋다. 함께 `(관측 GPU2 피크 ≤ 예측 상한)` 비율, 예측 상한−관측 피크의 평균·중앙값, 과소예측 최댓값, GPU1 대비 예측 상한 차이, 모델·조건별 표본 수와 성적을 남긴다. 모델별 손실·포함률을 먼저 계산한 뒤 모델을 같은 가중치로 평균한다. 조건 4코어와 별도 캠페인의 2~5개 조건을 독립 모델로 세지 않는다.

탐색적 채택 게이트는 `hgb_q90_delta`가 주 시험에서 **두 기준선 중 pinball이 낮은 쪽보다 ≥5% 낮고**, 모델 균등 포함률이 ≥90%이며, 각 encoder 모델 포함률이 ≥85%, 8개 중 ≥6모델에서 더 낮은 pinball을 얻고, 평균 예측 상한은 `unchanged_gpu1`보다 ≥10 MiB 낮은 것이다. Novel·space 조건 각각에서 모델 균등 포함률 ≥85% 및 두 기준선 중 낮은 pinball의 105% 이하를 추가 요구한다. 둘 중 어느 한 캠페인에서 실패하면 외부 조건 확장도 보류한다. 각 조건의 예측값과 목표값, fold·모델별 성적, 자료·규약·코드 해시를 저장한다. 게이트 통과도 **같은 호스트의 관측 피크 보조 신호** 채택만 뜻하고 운영 VRAM 호환성 승인이나 VM 형상 추천을 허용하지 않는다.
