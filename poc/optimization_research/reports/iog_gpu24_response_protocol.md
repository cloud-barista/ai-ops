# IOG RTX 4090 GPU 2→4 반응의 후향 탐색 규약 v1

## 목적과 해석 경계

동일 공개 벤치마크에서 현재 모델의 GPU 2장 관측과 목표 QPS만으로 GPU 4장 관측의 **요청 처리량**과 **TTFT p95** 상대 반응을 예측한다. 이는 기존 SQLite의 배치 추론 예측기를 외부 검증하는 실험이 아니다. IOG의 GPU 수는 별도 vLLM 서버의 tensor parallelism 설정과 결부된 보고 메타데이터이고, VM 복제·Scale-out 조치가 아니다. 원본 및 일부 결과를 자료 적합성 감사에서 이미 보았으므로 이 규약은 **채점 전 구현 선택 고정**이며 독립·블라인드 사전 등록이 아니다. 결과를 보고 특성·α·기준선을 바꾸지 않는다.

## 원본과 관측 단위

- 생산자 [IOG Foundation 저장소](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/README.md)의 commit `4990a807c6f1971edda635c18d98550059782eb8`에서 `results/*.json` 120개를 읽는다. 원본의 경로별 SHA-256을 기록하고, `path + " " + sha256`을 경로순 정렬·줄바꿈 결합한 manifest SHA-256이 `92ffc3479de653491c7f252c88adfc3ff9c1e231ad23672978b8d3962d32f215`인지 확인한다. 파일 크기 합계는 182,641 bytes여야 한다. 파일 하나에 JSON 객체가 여러 줄인 경우 줄마다 별도 실행으로 읽는다. 원본은 압축 아카이브와 해시 목록으로 보존한다.
- 관측 *셀*은 `(model_id, request_rate, gpu_name, gpu_count)`다. 모든 선택 행은 filename의 `_sharegpt_`, `backend=vllm`, `num_prompts=200`, `gpu_name=NVIDIA-GeForce-RTX-4090`, `gpu_count∈{2,4}`, `request_rate∈{1,4,8,16,32}`를 만족해야 한다. 모델은 `deepseek-ai/DeepSeek-R1-Distill-Llama-8B`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` 세 개로 고정한다.
- 같은 셀에 여러 실행이 있으면 `date`(원본 `YYYYMMDD-HHMMSS`)가 가장 이른 것을 고른다. 같으면 원본 경로의 사전순, 그래도 같으면 파일 내 줄 번호순으로 고른다. 성능 결과를 보고 실행을 고르지 않는다. 선택 뒤 모델당 목표 QPS 5개와 각 QPS의 GPU 2/4 두 셀, 총 **30셀·15쌍**이 정확히 있어야 한다. 중복이나 결측으로 개수가 달라지면 채점을 중단하고 규약을 사후 수정하지 않는다.
- 선택한 실행은 `completed=200`, 양수·유한 `request_throughput`, `p95_ttft_ms`, `total_input_tokens`, `total_output_tokens`를 가져야 한다. 쌍의 `total_input_tokens`가 다르면 채점을 중단한다. 총 입력 토큰 수가 같아도 개별 요청·프롬프트 ID는 원본 JSON에 없으므로 같은 200개 프롬프트라고 증명할 수 없다. 기록된 출력 토큰 차이는 따로 민감도로 분석한다. 목표 GPU4의 처리량·TTFT와 출력 토큰 수는 특성이나 모델 선택에 사용하지 않는다.

## 모델 전체 제외 평가

모델 하나의 QPS 다섯 쌍을 한꺼번에 제외하고 나머지 두 모델의 10쌍만으로 모델을 적합한다. 세 모델을 각각 제외하는 **3분할**이며 QPS 행이나 같은 모델의 반복 실행을 학습·시험에 섞지 않는다. 세 모델 모두 DeepSeek-R1-Distill 계열(기반 모델은 Llama/Qwen)이라 모델 전체 제외도 계열 전체 제외 검증은 아니다. 시험 한 쌍의 입력은 `x = [log(목표 QPS), log(GPU2 요청/초), log(GPU2 TTFT p95 ms)]`다. 학습 타깃은 각각 `y_R=log(GPU4 요청/초 ÷ GPU2 요청/초)`, `y_T=log(GPU4 TTFT p95 ÷ GPU2 TTFT p95)`다. 예측 GPU4 수치는 GPU2 관측값에 `exp(ŷ)`를 곱한다.

고정 비교군과 학습기는 다음 다섯 가지다.

1. `anchor_keep`: 두 상대 반응 모두 로그 0. 현재 GPU2 값을 그대로 유지한다.
2. `proportional`: 처리량 로그 반응 `log(2)`(GPU 2→4 비례), TTFT 로그 반응 0.
3. `capped_offered_load`: 설정 QPS `λ`를 알 때 처리량 예측을 **`min(2·R2, max(λ, R2))`**로 고정한다. 여기서 `R2`는 관측 GPU2 요청/초다. 200개 요청의 유한 표본에서는 실현 처리율이 설정 QPS를 약간 넘을 수 있어 예측이 `R2`보다 작아지지 않게 한다. TTFT 로그 반응은 0이다. λ만으로 GPU4의 결과를 추정하지 않는다.
4. `train_mean`: 훈련 두 모델의 10쌍에서 각 로그 반응의 산술평균. 모델당 QPS 5개이므로 두 모델은 같은 가중치다.
5. `ridge`: 위 세 입력 특성을 **훈련 10쌍에서만** 평균 0·모표준편차 1로 표준화한다(분산 0 특성의 scale은 1). 각 로그 타깃에 독립적인 `Ridge(alpha=1.0, fit_intercept=True)`를 적합한다. 모델 ID, GPU4 값, 출력 토큰 수, 날짜, 가격을 특성에 넣지 않는다. 난수나 검증 선택은 없다.

각 방법·모델별로 QPS 5개의 `abs(ŷ_R−y_R)` 및 `abs(ŷ_T−y_T)` 평균을 기록한다. 이것이 각각 **요청/초와 TTFT p95의 평균 절대 로그오차(MALE)**다. 세 모델의 모델별 MALE을 동일 가중 평균한 값을 전체 기술 통계로 기록하고, 모델별 값·실제/예측 로그 반응·선택 원본 경로를 보존한다. 두 지표를 합친 임의 점수나 유의성 검정은 만들지 않는다. 더 낮은 MALE가 양 지표에서 일관되는지 서술하되, 소수 모델의 우위를 일반화하지 않는다.

## 출력 토큰 수 민감도와 산출물

사전에 고정한 민감도 부분집합은 각 쌍에서 `abs(log(GPU4 total_output_tokens ÷ GPU2 total_output_tokens)) ≤ log(1.05)`인 경우다. 이를 고를 때 GPU4 출력 길이는 **평가 뒤의 층화 변수**로만 사용한다. 전체 15쌍으로 적합한 각 분할 모델을 그대로 두고 이 부분집합에서만 MALE을 다시 집계한다. 각 모델의 남은 QPS 수와 출력 토큰 수 비율의 범위도 보고한다. 한 모델의 남은 쌍이 0개면 해당 모델·전체 민감도 MALE은 `null`로 기록하고 순위를 주장하지 않는다. 특성·훈련 셀·예측값을 이 민감도 때문에 바꾸지 않는다.

점수 전에 이 규약 파일 SHA-256, 채점 스크립트 SHA-256, 120개 원본 해시 목록 및 압축 아카이브 SHA-256을 `poc/optimization_research/artifacts/iog_gpu24_response_v1/freeze_manifest.json`에 기록한다. 채점 명령은 이 해시들을 재확인하고 불일치 시 실패한다. 결과는 `selected_cells.csv`, `predictions.csv`, `per_model_metrics.csv`, `metrics.json`, 해석 보고서로 남긴다. 외부 원시 행을 KHU SQLite와 합쳐 학습하지 않는다.
