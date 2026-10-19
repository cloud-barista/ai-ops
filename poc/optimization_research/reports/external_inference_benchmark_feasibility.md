# 공개 AI 추론 벤치마크의 형상 추천 검증 적합성: MLPerf Inference v5.0

## 판정

- **기존 KHU SQLite 성능 예측기와 8개 GPU/CPU 형상 추천기의 독립 검증: NO-GO.** 모델 ID와 GPU 제품이 정확히 겹치는 사례가 각각 0개다. MLPerf의 Server 지연시간은 부하를 건 요청의 지연시간이고, KHU 타깃은 로컬 배치 실행의 `latency_p95_ms`다. 처리량 단위에도 `Tokens/s`와 `Queries/s`가 섞여 있다. 사전 변환이나 단순 이름 매칭으로 같은 라벨이라고 취급할 수 없다.
- **별도의 MLPerf 내부 탐색 연구: 제한적 GO.** 공식 결과의 시스템·모델·가속기 수·Server 처리량을 개별 LoadGen 로그의 지연시간과 결합할 수 있다. 같은 벤치마크 버전과 시나리오 안에서 관측 형상 간의 *기술적* 용량·SLO 선택 실험은 가능하다. 그러나 아래의 엄격한 스택 일치 기준에서 가속기 수가 다른 비교 묶음은 **3개, 7개 결과 행**뿐이다. 일반적인 VM 형상 학습이나 스케일 효과 추정의 충분한 학습 세트로 보아서는 안 된다.

## 원본, 크기, 사용 조건

공식 [MLCommons Inference v5.0 결과 저장소](https://github.com/mlcommons/inference_results_v5.0/tree/0bc17ab3f4b33f730d7869e04cc29cb70835e513)의 [`summary_results.json`](https://github.com/mlcommons/inference_results_v5.0/blob/0bc17ab3f4b33f730d7869e04cc29cb70835e513/summary_results.json)을 고정 commit `0bc17ab3f4b33f730d7869e04cc29cb70835e513`에서 검사했다. 원본은 **27,696,289 bytes**(약 26.4 MiB), SHA-256 `5a9edf18b4272a85eaac8e15a96680b1562dee73d2fdae95efdfcd29ca63e570`, JSON 객체 **17,454개**다. 이 파일과 아래 예시 로그 2개만 내려받으면 500 MB보다 훨씬 작다. 원본은 이 저장소에 추가하지 않았다.

해당 결과 저장소 루트에는 `LICENSE`가 없고 GitHub License API도 `Not Found`를 반환한다. [MLCommons 제출 정책](https://github.com/mlcommons/policies/blob/master/submission_rules.adoc)은 게시 후 코드·결과가 [MLPerf Terms of Use](https://drive.google.com/file/d/18CFB2RAb4yVY-lu6rqR6i2vEANEpTyO2/view?usp=sharing) 아래 공개·사용 가능하다고 설명한다. 조직 소개의 일반적인 Apache-2.0/CC BY 4.0 문구를 이 결과 파일의 개별 라이선스로 단정할 수 없다. [결과 표기 지침](https://github.com/mlcommons/policies/blob/master/MLPerf_Results_Messaging_Guidelines.adoc)은 MLPerf 결과와 비-MLPerf 결과의 성능 비교를 금지하고, MLPerf 내부 비교에도 버전·시나리오·칩 수 등의 차이를 명시하도록 한다. 따라서 결과를 재배포하거나 KHU 벤치마크 수치와 직접 비교하지 않고, 내부 연구에서 원본 링크·제출 ID·조건을 보존한다.

## 확인한 스키마와 실제 측정값

`summary_results.json`의 주요 열은 `ID`, `Submitter`, `Category`, `Suite`, `Model`, `Scenario`, `Nodes`, `Processor`, `host_processor_core_count`, `Accelerator`, `a#`, `Software`, `Location`, `Performance_Result`, `Performance_Units`다. **Server** 행은 409개(Closed 393, Open 16)이며, 모두 `compliance=1`, `errors=0`, `inferred=0`이다. 그중 388개 행에 가속기 이름과 개수가 있다. Server 처리량의 단위는 `Tokens/s` 266개, `Queries/s` 143개다. 요약 파일에 서버 지연시간 열은 없다.

공식 개별 `Location/performance/run_1/mlperf_log_summary.txt`에는 처리량과 지연시간이 **같은 실행**으로 기록된다. 예를 들어 NVIDIA Closed, Llama2-70B-99, TensorRT 10.8/CUDA 12.8, 같은 Intel Xeon Platinum 8570 호스트 프로세서의 다음 두 결과가 있다.

| 공식 결과 ID · 시스템 | GPU 수 | Server 토큰/초 | 같은 실행의 전체 요청 p95 |
| --- | ---: | ---: | ---: |
| [`5.0-0055`, B200×1](https://github.com/mlcommons/inference_results_v5.0/blob/0bc17ab3f4b33f730d7869e04cc29cb70835e513/closed/NVIDIA/results/B200-SXM-180GBx1_TRT/llama2-70b-99/Server/performance/run_1/mlperf_log_summary.txt) | 1 | 12,078.40 | 90.943 s |
| [`5.0-0056`, B200×8](https://github.com/mlcommons/inference_results_v5.0/blob/0bc17ab3f4b33f730d7869e04cc29cb70835e513/closed/NVIDIA/results/B200-SXM-180GBx8_TRT/llama2-70b-99/Server/performance/run_1/mlperf_log_summary.txt) | 8 | 98,443.30 | 93.973 s |

두 로그 모두 `Result is : VALID`이며, 원본 p95 단위 ns를 초로 나눴다. 두 실행의 `target_qps`도 각각 44.5와 364로 다르다. 이는 동일 부하에서 GPU 수만 바꾼 통제 실험이 아니므로 토큰/초 차이를 GPU 수의 순수 인과효과라고 해석할 수 없다. LLM의 TTFT·TPOT SLO와 전체 요청 p95 역시 구분해야 한다.

## KHU 데이터와의 교집합 및 분할 가능성

로컬 `poc/optimization_research/data/audit_v1/measurements.parquet`에는 모델 ID 11개, 장치 이름은 CPU·NVIDIA GeForce RTX 5070 Ti·NVIDIA RTX PRO 5000 Blackwell·unknown이 있다. MLPerf v5.0 Server 포함 요약에는 `bert-99`, `gptj-99`, `llama2-70b-99` 같은 별도 모델 19종과 H100/H200/B200 등 다른 장치가 있다. **정확한 모델 ID 교집합 0, 정확한 가속기 이름 교집합 0**이다. BERT 계열이라는 이름의 유사성도 동일 모델·작업을 뜻하지 않는다. KHU `replica_count=1/2 × cpu_core_limit=2/4/8/16`의 고정 형상 격자, 입력 길이, 반복 실행, 같은 호스트 조건은 공개 요약에서 복원할 수 없다.

전체 Server 결과는 13개 모델, 22개 제출 기관, 17개 비어 있지 않은 가속기 종류를 포함한다. `Submitter + Category + Model + Accelerator + Software + Processor + host_processor_core_count + host_processors_per_node + operating_system + Nodes`를 고정해도 GPU 개수가 달라지는 묶음은 **3개/7행**뿐이다: FlexAI의 Llama2-70B-99/H100, NVIDIA의 Llama2-70B-99/B200, NVIDIA의 Llama2-70B-99.9/B200. 이 정도로는 모델이나 하드웨어 단위 독립 테스트를 포함한 안정적인 자원 반응 모델을 학습하기 어렵다. 전체 Server 행을 무작위로 나누면 동일 제출 시스템과 소프트웨어가 양쪽에 들어가 평가가 부풀려질 수 있으므로, 후속 탐색을 한다면 최소한 `Submitter + System + Model` 묶음으로 분리하고 중복·정확도 등급(99/99.9)·단위를 먼저 정리해야 한다. 그 분할의 유효 표본 수는 이번 검사에서 확정하지 않았다.

**다음 연구 결정:** 현재 저장 모델의 외부 정확도 검증으로 쓰지 않는다. MLPerf 자체를 별도 연구 대상으로 삼는다면, 먼저 Server 실행 로그 전수의 존재·VALID 여부·p95/TTFT/TPOT와 처리량 단위를 감사하고, 독립 시스템 홀드아웃에서 단순 하드웨어 수 기준선보다 개선되는지 시험한다. 합성 SLO를 만들 경우 그것이 실제 사용자의 요구 분포가 아님을 표시한다. GPU 수가 다른 관측이 적으므로 배포나 Scale-out/in 효과 검증은 여전히 보류한다.
