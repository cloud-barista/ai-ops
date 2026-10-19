# GPU 1개 측정 후 GPU 2개 프로파일 시간 예산: 고정 탐색 규약

2026-09-29. 이 규약과 구현 해시를 **후보 학습·후행 시험 점수 산출 전에** 고정한다. 다만 [기존 적격성 감사](replica_profiling_budget_feasibility_2026_09_29.md)가 같은 자료의 단순 기준선과 일부 시험 정답을 이미 열람했다. 따라서 이번 결과는 후향적 탐색이며 독립 확인이나 새 VM의 준비시간 예측이 아니다.

## 질문과 출처

대상 작업의 GPU 1개 프로파일이 끝난 직후, GPU 2개 프로파일을 수행할 로컬 시간 예산의 90분위를 예측한다. `khu-hf-local-exhaustive/executions.sqlite3`의 물리 `execution_measurements`와 고정 추출 `data/audit_v1/measurements.parquet`만 사용한다. 고정 추출 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 성공·적격·`host=icns`·`device=cuda` 측정에서 `(model_id, model_revision, workload_group, cpu_core_limit)`별 GPU replica 1/2를 정확히 한 행씩 결합한다. 예상 지지량은 2,300쌍, encoder 추론 8모델/2,048쌍, Qwen causal LM 1모델/252쌍이다. GPU 1개 `finished_at <` GPU 2개 `started_at`이어야 한다.

정답 `E₂`는 GPU 2개 행의 `finished_at - started_at` 초다. GPU 1개 대응값은 `E₁`이다. 실행 제어 구간으로서 자식 프로세스 시작·종료를 포함하지만 VM 부팅, 이미지 배포 완료, 오토스케일 명령 효과, 사용자 요청 지연은 아니다. 두 행의 작업 조건이 다른 쌍은 거부한다. 프로파일 비용은 예시 5초 한계만 판정한다. 그 한계는 사용자 SLO가 아니다.

## 결정 시점의 입력과 누출 차단

대상 GPU 1개 행에서만 `E₁`, `wall_seconds`, `load_seconds`, `compute_seconds`, `result_json.model_parameter_bytes`를 읽는다. 사전 설정 `batch_size`, `max_length`, `request_concurrency`, `repeats`, `cpu_core_limit`도 허용한다. 시간·바이트·크기 변수에는 `log1p`를 적용하고, 배치·길이·반복·CPU 코어에는 `log`를 적용한다. 모델 ID·프로필 ID·작업 그룹 ID·수집 순서·사용자 목표 SLO는 학습 특징에서 뺀다. GPU 2개 행은 짝 확인·정답 시각 이외에 어떤 숫자 결과도 읽지 않는다. 특히 GPU 2개 `result_json`, `node_summary_json`, 처리량, `wall_seconds`, `running`·`after` 계측은 입력 금지다.

## 분할·방법·점수

시간 절단은 `2026-09-11T13:00:00Z`다. GPU 2개가 절단까지 끝난 encoder 쌍만 훈련하고, GPU 1개가 절단 뒤 시작한 encoder 쌍만 시험한다. 각 대상 모델을 양 호스트·전체 시간에서 완전히 훈련 제외하는 8개 fold를 만든다. 주 원본은 한 호스트뿐이고, 훈련 donor는 나머지 7개 encoder다. 예상 훈련 풀 1,044쌍, 시험 1,004쌍이다. Qwen 시험 252쌍은 같은 작업 종류 donor가 없어 숫자를 내지 않고 `ABSTAIN`한다. `novel-matrix`, `space-filling`, 반복 `live` 캠페인은 주 훈련·시험에 넣지 않는다.

고정 학습기는 `HistGradientBoostingRegressor(loss='quantile', quantile=0.9, max_iter=100, learning_rate=0.05, max_leaf_nodes=8, min_samples_leaf=40, l2_regularization=10, early_stopping=False, random_state=42)`다. 정답은 `Δ=E₂−E₁`이고 예측은 `max(0.001, E₁+Δ̂)`초다. 한 번 고정한 특징과 초매개변수를 시험 점수에 따라 바꾸지 않는다. 기준선은 (1) `E₁ + donor median(Δ)`, (2) **강한** `E₁ + donor p90(Δ)`, (3) `E₁ × donor p90(E₂/E₁)`, (4) 동일 특징의 `StandardScaler + Ridge(alpha=10)` 평균 Δ 예측에 donor 훈련 잔차 p90을 더한 것이다. 모두 양수 하한 0.001초를 적용한다. donor 분위수는 `numpy.quantile(method='linear')` 기본값을 쓴다. Ridge 잔차는 donor의 in-sample 예측으로 구하며, 이를 보수적인 독립 보정으로 주장하지 않는다.

1차 지표는 모델별 0.9 pinball 손실의 **8모델 균등 평균**이다. 비교 상대는 두 p90 기준선과 Ridge p90 중 평균 손실이 최소인 방법이다. 보조 지표는 전체 시험 피복률 `P(E₂≤예측)`, 모델별 손실·피복률, 과대 예산폭 `mean(max(예측−E₂,0))`, `예측≤5초` 승인 수·실제 `E₂>5초` 오승인 수다. 동일 모델 128행을 독립 앱으로 취급하지 않는다. 원본·프로토콜·코드 SHA-256, 파이썬·라이브러리 버전, 각 fold donor·시험 건수, 행별 예측을 저장한다.

## 사전 채택 기준

학습기를 연구용 프로파일 시간 예산 후보로 남기려면 **모두** 만족해야 한다: (a) 1차 손실이 최선 p90 기준선보다 상대 5% 이상 낮다; (b) 시험 전체 피복률이 88% 이상이다; (c) 8모델 중 6모델 이상에서 같은 기준선보다 pinball이 낮다; (d) 어떤 모델에서도 기준선보다 pinball이 20%를 초과해 나쁘지 않다; (e) 5초 예시 예산의 오승인은 4건 이하이다. 하나라도 실패하면 **미채택**하고 시험값에 맞춰 특징·문턱을 바꾼 재시험을 독립 결과로 제시하지 않는다. 통과하더라도 한 호스트·한 수집기의 로컬 프로파일링 시간에만 적용한다.
