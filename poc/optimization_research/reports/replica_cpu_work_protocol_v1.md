# GPU 1개 계측으로 GPU 2개 프로파일의 CPU 작업량 예측: 사전 규약 v1

2026-09-29. 예측 대상은 같은 `icns` 호스트에서 동일 AI 배치 추론 프로필을 GPU 1개로 완료한 뒤 GPU 2개를 각각 새 자식 프로세스로 실행했을 때의 **두 자식 `cpu_seconds` 합계**다. 이 수치는 각 프로세스의 `time.process_time()`으로 잰 모델 로드·추론 포함 CPU 시간의 합이다. 실제 VM의 최소 CPU 코어 수, 순간 CPU 사용률, 사용자 지연, 인과적 `SCALE_OUT` 효과는 정답에 없다. 기존 GPU 2개 측정 결과는 시험 정답에만 사용한다.

## 원본·짝·시점

- 고정 `data/audit_v1/measurements.parquet` SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 원본은 `khu-hf-local-{exhaustive,novel-matrix,space-filling}` SQLite의 물리 실행이다. `eligible`, `success=1`, `device=cuda`, `host=icns`, `replica_count in {1,2}`로 한정한다. 정확한 짝 키는 `(campaign,host,model_id,model_revision,workload_group,cpu_core_limit)`이다. 짝에 두 수량이 정확히 하나씩 없으면 중단한다.
- GPU1과 GPU2의 batch·길이·동시성·반복 수·dataset·CPU affinity/thread·작업 종류는 같아야 하고, 물리 GPU 인덱스는 `[0]`와 `[0,1]`이어야 한다. GPU1 기록 종료가 GPU2 기록 시작보다 앞서야 한다. GPU1 `cpu_seconds`는 결과 JSON과 열에서 일치하고 양수여야 한다. GPU2 JSON은 서로 다른 GPU 0·1의 `replica_results` 두 개를 담아야 하며, 두 양수 `cpu_seconds`의 합은 GPU2 결과 JSON 및 열의 `cpu_seconds`와 기록 소수점 오차 안에서 일치해야 한다. 자식 배열 순서는 특징이나 라벨의 의미에 영향을 주지 않는다.
- 주 캠페인은 9모델·2,300짝이다. 8 encoder 모델 각 256짝, 유일한 causal LM Qwen 252짝이다. 학습·주 평가는 encoder 8모델 2,048짝만 사용한다. Qwen은 동종 학습 모델이 없어 `ABSTAIN`하며 2배 기준선의 분포만 참고한다. 별도 입력 조건 `novel-matrix` encoder 64짝, `space-filling` encoder 160짝은 **같은 호스트·같은 모델**의 보조 시험이며 주 캠페인보다 먼저 수집됐다. 이 SQLite는 다른 연구에서 이미 여러 번 열렸으므로 모든 결과는 탐색적이며 새 모델·호스트 독립 검증이 아니다.

## 예측 시점·방법

예측 시점은 대상 작업의 GPU1 측정 종료 직후, GPU2 새 자식 프로세스 실행 전이다. 네 방법 모두 대상 GPU1의 `cpu_seconds`를 사용한다. 학습기는 대상 모델을 통째로 제외한 나머지 encoder 7모델의 주 캠페인 짝만 이용한다. 대상 모델 ID, GPU2 결과, 대상 이후의 노드 계측, 생성 SLO 및 시험 캠페인 라벨은 특징이나 학습에 사용하지 않는다.

1. `twice_gpu1`: GPU1 CPU 시간의 2배. 두 자식이 같은 전체 프로필을 수행한다는 구조적 기준선이다.
2. `donor_ratio`: donor의 `log(GPU2 합계 / GPU1)` 중앙값을 구해 GPU1에 곱한다. 모든 donor 조건을 동등하게 취급한다.
3. `ridge_ratio`: donor의 같은 로그 비율을 `StandardScaler` 뒤 `Ridge(alpha=10)`으로 예측한다.
4. **주 학습기 `hgb_ratio`:** donor의 같은 로그 비율을 `HistGradientBoostingRegressor(loss='squared_error', max_iter=100, learning_rate=0.05, max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=10, early_stopping=False, random_state=42)`로 예측한다.

두 회귀기의 입력은 모두 GPU1에서 관측한 `log(cpu_seconds)`, `log1p(load_seconds)`, `log1p(compute_seconds)`, `log1p(model_parameter_bytes / 2^20)`과 실행 전 프로필의 `log(batch_size)`, `log(max_length)`, `log1p(request_concurrency)`, `log(repeats)`, `log(cpu_core_limit)` 아홉 수치다. 각 입력은 양수·유한해야 한다. `gpu1_cpu_seconds * exp(예측 로그 비율)`로 복원하며 계산이 유한·양수가 아니면 중단한다. 추가 특징, 파라미터 탐색, 시험 점수에 맞춘 사후 보정은 하지 않는다.

## 점수와 채택 게이트

주 지표는 `|log(예측 GPU2 CPU초 / 관측 GPU2 CPU초)|`의 **모델 균등 평균**이다. 모델 안에서 동일 작업의 CPU core 설정을 먼저 평균한 뒤 작업을 평균한다. 보조 지표는 모델별 평균 절대 로그오차, 중위 상대 오차, 90분위 절대 로그오차, 과소예측 비율, CPU초 MAE다. 주와 두 보조 캠페인의 모든 예측 행·모델별 점수·해시·도구 버전을 저장한다.

연구용 채택은 고정 `hgb_ratio`가 주 시험에서 세 대조군 중 가장 낮은 모델 균등 평균 절대 로그오차보다 **5% 이상 낮고**, encoder 8모델 중 **6모델 이상**에서 가장 좋은 대조군보다 낮으며, 어느 모델도 해당 모델의 최고 대조군보다 **20% 초과 악화하지 않고**, 두 별도 입력 캠페인 각각에서 최고 대조군 손실의 **105% 이하**일 때만 한다. 실패하면 결과를 그대로 기록하고 학습기를 미채택한다. 통과하더라도 한 호스트의 정적 프로파일링 CPU 시간 보조 예측일 뿐 실제 VM 형상·배포·스케일 승인에는 쓰지 않는다.

점수 계산 전에 원본 짝의 적격성 감사, 이 규약과 평가 코드 및 원본 SHA-256을 `freeze.json`에 기록한다. 평가 명령은 해시 불일치나 불완전한 짝에서 중단한다. 결과를 본 뒤 이 규약을 고쳐 같은 평가를 독립 시험이라고 부르지 않는다.
