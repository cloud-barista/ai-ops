# AgentPerfBench A100 4→8 TTFT·TPOT 반응: 점수 전 고정 규약

작성일: 2026-09-29. **이 문서는 예측 점수 계산 전에 고정한다.** [지원량 감사](agentperf_component_next_task_feasibility.md)에서 확인한 좁은 쌍을 대상으로, 한 형상 계측 후 다른 형상의 두 지연 성분을 예측하는 *탐색적* 연구를 한다. 데이터는 [AgentPerfBench 공식 카드](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/README.md)의 `trace_replay/summary.parquet`이며 revision `722f2b9d8a6acd222811f4e210a96865f774f6ee`, SHA-256 `568efbdae473a830ba792317fb09cfba0ea4f667db67b93d28e72637a5c71d1e`이다. KHU SQLite와 병합하지 않는다.

## 정답과 평가 모집단

- 동일 `(model, engine, profile, concurrency, num_requests)`에 `A100-40GBx4`와 `A100-40GBx8`이 각각 정확히 하나 있는 실행만 짝짓는다. 두 행 모두 `duration_s`, `num_requests`, `request_throughput`, `input_token_throughput`, `output_token_throughput`, `p90_ttft_ms`, `p90_tpot_ms`, `p90_e2el_ms`가 양수·유한하고 `|request_throughput×duration_s−num_requests|≤1e-5`여야 한다. `input_token_throughput×duration_s`는 두 형상 사이 최대/최소 `≤1.00001`임을 확인하고, 아니면 중단한다. 다른 GPU 형상의 결과로 쌍을 선별하지 않는다. 예상 주 모집단은 **130쌍/5모델**, 모두 vLLM이다.
- 목표는 A100 8장 실행의 `log(p90_ttft_ms)` 및 `log(p90_tpot_ms)` 두 개다. 두 지표는 각각 성공 요청의 p90이며 서로 더하거나 평균 출력 길이를 곱해 p90 E2EL을 복원하지 않는다. GPU 개수 4→8은 텐서 병렬도 변경이지 VM 복제 조치가 아니다.
- `output_token_throughput×duration_s`로 복원한 **대상 형상**의 출력 토큰 총량은 입력 및 주 모집단 선별에 쓰지 않는다. 두 형상 출력 총량의 최대/최소 비율 `≤1.05` 및 `≤1.01`을 **사후 민감도 집단**으로만 적용한다. 예상 건수 127 및 83쌍이다. 민감도에서는 주 모집단으로 학습한 예측을 그대로 필터링해 점수만 다시 계산하고 재학습하지 않는다.

## 입력 시점과 특징

결정 시점은 **같은 프로필을 A100 4장으로 한 번 계측한 후**다. 허용 입력은 모델 카드의 정적 정보(계열, 총·활성 파라미터 수, mxfp4 사용 여부), 계획된 `profile`, `concurrency`, `num_requests`, A100 4장 실행의 `log(p90_ttft_ms)`, `log(p90_tpot_ms)`, `log(input_token_throughput/request_throughput)`, `log(output_token_throughput/request_throughput)`다. 파라미터 수는 카드에 기재된 수치로 고정한다: Llama-3.1-8B `8/8`, Llama-3.1-70B `70/70`, Mixtral-8x7B `46.7/12.9`, Qwen3.5-9B `9/9`, gpt-oss-20b `21/3.6`(총/활성 B). gpt-oss만 mxfp4=1, 나머지는 BF16/mxfp4=0이다. `model` ID는 분할·보고 키로만 사용하고 특징에 넣지 않는다. `engine`과 두 하드웨어는 이 집단에서 상수라 특징에서 뺀다.

**A100 8장 실행의** p90 지연, 토큰 처리량, 출력 토큰 수, 성공 요청 수, 실행시간, `run_id`, 또는 다른 목표 결과에서 파생된 어떤 값도 특징에 넣지 않는다. 앵커의 출력 길이는 앵커 계측 이후에만 알 수 있으므로 이 평가는 등록 시점 무계측 추천의 정확도가 아니다. 모든 방법은 같은 앵커 관측을 받는다.

## 분할과 고정 방법

주 평가는 **모델 전체 제외 5분할**이다. 각 fold에서 시험 모델의 모든 프로필·동시성·행을 훈련·전처리 적합·모형 선택에서 제외한다. 추가 스트레스는 두 Llama 모델을 함께 제외하고 나머지 세 모델에서 학습한다. 범주형 `model_family`, `profile`은 각 훈련 fold의 `OneHotEncoder(handle_unknown='ignore')`로 인코딩하고, 수치형은 훈련 fold로만 `StandardScaler`를 적합한다. ExtraTrees는 스케일링하지 않은 수치형과 같은 범주 인코딩을 사용한다. 결측·비유한 정적 특징이 생기면 중단하며 시험 집단에서 채우지 않는다. 난수 시드는 42, 병렬 작업 수는 1이다.

방법은 모두 TTFT와 TPOT를 별도로 출력하며, 대상 로그 지연값으로 비교한다.

1. `anchor_copy`: 각 대상 로그 지연 = 같은 성분의 앵커 로그 지연.
2. `half_gpu`: 각 대상 로그 지연 = 같은 성분의 앵커 로그 지연 − `log(2)`. GPU 2배가 지연 1/2을 뜻하지는 않으며 비교용 낙관적 기준선이다.
3. `donor_global`: 훈련 모델마다 각 성분의 `log(8장/4장)` 반응 중앙값 하나를 만든 다음, 모델 중앙값들 사이 중앙값을 앵커에 더한다. 훈련 행이 많은 모델에 큰 가중을 주지 않는다.
4. `donor_profile`: 시험 프로필을 가진 훈련 모델이 최소 3개면 각 훈련 모델의 그 프로필 반응 중앙값들 사이 중앙값을 쓴다. 3개 미만이면 `donor_global`로 되돌린다.
5. `direct_ridge`: 각 성분의 대상 로그 지연을 입력에서 바로 Ridge(`alpha=10`, 절편 포함)로 적합한다.
6. `direct_et`: 같은 대상 로그 지연을 ExtraTreesRegressor(`n_estimators=200`, `min_samples_leaf=5`, `max_features=1.0`, `random_state=42`, `n_jobs=1`)로 적합한다.
7. `ratio_ridge`: 각 성분의 `log(8장/4장)`을 위 Ridge로 적합한 뒤 해당 앵커 로그 지연에 더한다.
8. `ratio_et`: 같은 반응을 위 ExtraTrees로 적합한 뒤 앵커에 더한다.
9. **연구 후보 `phase_coupled`:** TTFT·TPOT 로그 반응을 각각 `d_f`, `d_p`라 할 때, 공통 반응 `u=(d_f+d_p)/2`를 Ridge(`alpha=10`)로, 성분 차이 `v=(d_f−d_p)/2`를 Ridge(`alpha=100`)로 적합한다. 두 모형은 같은 입력·전처리를 쓴다. `d̂_f=û+v̂`, `d̂_p=û−v̂`를 앵커 로그 지연에 더한다. 차이 성분을 더 강하게 수축하는 결합법이며 학술적 최초성을 주장하지 않는다.

점수 확인 후 특징, 필터, 하이퍼파라미터, 비교군 또는 분할을 바꾸면 새 탐색으로 명시하고 이 규약의 결론을 소급 바꾸지 않는다.

## 점수와 사전 결정 기준

- 주 점수는 **각 시험 모델 안에서** `|log 예측−log 관측|`의 평균(MALE)을 TTFT·TPOT별로 계산한 다음 5모델에 같은 가중치를 준 값이다. 후보행 가중 MALE, 5개 모델별 값, Llama 계열 전체 제외의 두 모델별·평균 점수도 낸다. 두 성분을 한 숫자로 합쳐 약한 성분을 숨기지 않는다.
- 관측한 두 로그 반응의 부호가 반대면 `conflict`다. 예측한 부호가 반대인지의 진짜 양성·거짓 양성·거짓 음성·진짜 음성, 재현율과 거짓 경보율을 기록한다. 작은 변화에 민감한 해석을 피하려고 두 관측 로그 반응의 절댓값이 각각 `log(1.05)`를 넘는 `large_conflict`를 따로 기록한다. 같은 문턱을 예측에도 적용한 분류표와 재현율도 낸다. 모델별로 양성이 0개면 해당 재현율을 `null`로 두고, 총계의 재현율로 대체하지 않는다. 이 분류는 벤치마크에서의 방향 판별이지 사용자 SLO 승인 성능이 아니다.
- 5%·1% 출력 차이 집단은 **동일한 5분할 주 예측**을 재채점한다. 대상 출력 토큰 수가 입력으로 들어간 적이 없음을 검사한다. 각 집단의 수·모델별 지원·모델 균등 MALE·충돌표를 보고한다.
- `phase_coupled`를 다음 데이터 수집을 우선할 **후속 연구 후보**로 남기려면, (a) 두 성분의 모델 균등 MALE이 각각 이 실험의 다른 모든 방법 중 가장 낮은 값보다 **최소 5% 낮고**, (b) 두 성분 각각 5모델 중 적어도 3모델에서 `direct_et`보다 낮고, (c) Llama 계열 제외 및 1% 출력 차이 집단에서 두 MALE이 각각 `direct_et`보다 **10% 이상 높지 않으며**, (d) 주 집단의 실제 `conflict` 재현율이 `direct_et` 이상이고 거짓 경보율이 `direct_et+0.10` 이하이어야 한다. 하나라도 실패하면 `do_not_prioritize`다. 이 판정은 **탐색 후속 작업의 우선순위**일 뿐 운영 채택·안전 보증이 아니다. 독립 시험 모델은 5개뿐이고 성공 실행만 공개됐으므로 좋은 결과가 나와도 별도 새 모델·하드웨어 검증이 필요하다.
