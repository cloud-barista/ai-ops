# 공개 다중 GPU 추론 벤치마크의 형상 연구 적합성

## 판정

[IOG Foundation의 LLM Inference Benchmarks](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/README.md)는 같은 모델과 ShareGPT 입력 조건을 여러 GPU 종류·수에서 측정한 **별도 온라인 서빙 연구 자료**로 쓸 수 있다. 기존 [MLPerf v5.0 감사](external_inference_benchmark_feasibility.md)의 엄격한 동일 스택 GPU 수 비교 3묶음·7결과보다 같은 작업의 형상 비교가 많다. 그러나 **현재 SQLite로 학습한 8형상 상대 반응 예측기의 직접 외부 정확도 검증에는 사용하지 않는다.** 모델 ID·GPU 제품 교집합이 각각 0개이고, 측정 대상·자원 형상·지연 지표가 다르다.

## 원본과 실제 비교 가능한 수

생산자 저장소의 [고정 commit `4990a807c6f1971edda635c18d98550059782eb8`](https://github.com/IOG-Foundation/llm-inference-benchmarks/commit/4990a807c6f1971edda635c18d98550059782eb8)에서 `results/*.json` 파일 **120개, 합계 182,641 bytes**를 직접 읽었다. 110개는 JSON 객체 1개, 10개는 확장자가 `.json`이어도 **줄당 객체 2개**라 전체는 실행 기록 130개다. `경로 + 각 파일 SHA-256`을 경로순으로 이은 문자열의 SHA-256은 `92ffc3479de653491c7f252c88adfc3ff9c1e231ad23672978b8d3962d32f215`다. 원본을 이 저장소에 복제하지 않았다. 저장소의 [LICENSE](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/LICENSE)는 Apache-2.0이다. 원래 ShareGPT 프롬프트의 재배포 조건은 따로 확인해야 하며 이 감사는 수치 요약만 읽었다.

모든 기록은 vLLM, ShareGPT 입력 200개, 목표 QPS `1/4/8/16/32` 중 하나다. 이 감사의 *관측 형상*은 JSON의 `(gpu_name, gpu_count)` 쌍이다. 이는 CPU·RAM·VM 노드·실행 명령까지 포함한 실제 인프라 형상이 아니다. `모델 ID × 입력 200개 × 목표 QPS`로 묶으면 **13모델·65묶음**이며 다음과 같다. 각 묶음의 총 입력 토큰 수는 같았지만 개별 프롬프트 ID는 JSON에서 확인되지 않았고, 24묶음의 출력 토큰 총수는 형상이나 반복 실행 사이에 달랐다.

| 묶음의 고유 GPU 형상 수 | 묶음 수 | 고유 형상 셀 | 해당 모델 수 |
| ---: | ---: | ---: | ---: |
| 1 | 40 | 40 | 8 |
| 2 | 10 | 20 | 2 |
| 4 | 15 | 60 | 3 |

형상이 둘 이상인 묶음은 **25개·80셀**이다. 같은 RTX 4090에서 GPU 수가 달라지는 비교는 **15묶음·45셀**이다. 그중 GPU `1/2/4`가 모두 있는 모델은 DeepSeek-R1-Distill-Llama-8B와 DeepSeek-R1-Distill-Qwen-7B **두 개**뿐이고, DeepSeek-R1-Distill-Qwen-14B는 `2/4/8`이다. 다른 GPU 종류와의 비교까지 포함해도 GPU 수 비교를 독립 모델 홀드아웃으로 검증하기에는 모델 수가 적다. 같은 형상 셀에 두 실행이 든 경우는 10개이므로 파일 수·실행 수·형상 셀 수를 혼용하지 않아야 한다.

130개 실행의 `date`는 모두 **2025-06-10 13:01:35–17:38:18** 형식의 같은 날짜다(시각대는 원본에 없다). 25개 다형상 묶음은 실제로 모델 **5개에서 각 QPS 5개를 반복**한 것이므로 독립 작업 25개로 세면 안 된다. 같은 모델의 QPS 행을 학습·시험에 나누면 해당 모델의 형상 반응이 양쪽에 들어간다. 같은 날의 전후 시간 분할도 다른 날·호스트로의 전이를 검증하지 못한다. 반복 기록 10쌍은 같은 모델·QPS·GPU 형상에 속하므로 양쪽 분할에 흩뜨려서는 안 된다.

130개 실행 모두 `completed=200`, `request_throughput`(요청/초), `output_throughput`(출력 토큰/초), `p95_ttft_ms`, `p95_tpot_ms`, `p95_itl_ms`가 있고 수치가 양수다. 예를 들어 Llama-8B의 QPS 16에서 RTX 4090 한 장은 `6.33` 요청/초·TTFT p95 `162.0 ms`, 두 장은 `6.77` 요청/초·`792.8 ms`였다([원본 1장](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/results/vllm_200prompts16qps_DeepSeek-R1-Distill-Llama-8B_sharegpt_1xNVIDIA-GeForce-RTX-4090.json), [원본 2장](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/results/vllm_200prompts16qps_DeepSeek-R1-Distill-Llama-8B_sharegpt_2xNVIDIA-GeForce-RTX-4090.json)). GPU를 늘렸을 때 두 지표가 함께 좋아진다고 가정할 수 없다는 예시다. 이는 tensor parallelism과 부하가 얽힌 관측 비교이지 VM Scale-out 인과 효과가 아니다.

## 현재 모델에 직접 연결할 수 없는 이유

- 로컬 `measurements.parquet` (`../data/audit_v1/measurements.parquet`)의 모델 11종은 IOG의 13종과 정확한 ID 교집합이 0개다. 로컬 GPU는 RTX PRO 5000 Blackwell·RTX 5070 Ti이고 IOG는 RTX 4090·H100 PCIe라 제품 교집합도 0개다.
- 로컬은 주로 고정 입력의 배치 forward 실행에서 `GPU replica 1/2 × CPU affinity 2/4/8/16`을 비교하며 타깃은 배치 `latency_p95_ms`와 `examples_per_second / batch_size`다. IOG는 vLLM 생성 요청의 TTFT/TPOT/ITL p95와 온라인 요청/초·토큰/초를 기록한다. 이 수치를 단위만 바꿔 같은 정답으로 만들 수 없다.
- IOG의 [설명](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/README.md)에 따르면 GPU 이름·수는 벤치마크 파일에 붙인 **메타데이터**이고 실제 병렬화는 별도의 `vllm serve` 설정에서 결정된다. 연구자가 모든 가용 GPU를 tensor parallelism에 썼다고 설명하지만, JSON에는 서버 시작 명령·CPU 코어 수·RAM·GPU 메모리/OOM·개별 요청 지연 원본·실제 VM 식별자·배포 또는 스케일 명령이 없다. 같은 `GPU 수`를 로컬 복제본 수나 VM 노드 수로 치환할 근거가 없다.

따라서 이 자료는 **별도 온라인 추론 도메인에서 단순 기준선과 자원 반응 방법을 새로 비교하는 탐색**에 한정한다. 이미 이 감사에서 전 파일과 비교 가능 범위를 확인했으므로, 같은 공개 수치에 맞춰 방법을 고른 뒤 해당 수치를 독립·사전 검증이라고 부르지 않는다. 실제 KHU 형상 추천·스케일 판단을 검증하려면 동일 모델·입력·소프트웨어의 GPU/CPU 형상별 사용자 요청 지연·처리량과 조치 전후 기록을 별도로 확보해야 한다.

후속 **방법론 스트레스 테스트 규약 후보**는 다음과 같다. 실행 전에 이 파일들의 commit·해시, 형상 키, 중복 처리, 비교군, 손실과 실패 판정을 동결한다. 셀에 두 실행이 있으면 가장 이른 `date`를 기본값으로 택하고 다른 실행은 반복 민감도로만 본다. RTX 4090의 GPU `1/2/4`가 공통인 두 모델만 별도 하위 집합으로 두고 모델 전체를 하나씩 제외한다. 다른 하위 집합에서 GPU `2/4` 비교를 추가할 수 있지만 GPU `1` 기준 반응과 섞지 않는다. IOG 훈련 모델에서 **방법 절차만 새로 적합**하고 KHU의 저장 계수·배치 지연 라벨은 사용하지 않는다. 모델별로 QPS를 동일 가중한 요청/초와 TTFT p95 로그오차를 단순 GPU 수 비례·앵커 비율 기준선과 함께 보고, GPU 수 증가 시 처리량 이득과 TTFT 악화도 따로 기록한다. 두 모델뿐인 공통 격자와 이미 열람한 원본 때문에 유의한 일반화 주장이 아니라 실패 양상을 찾는 후향 탐색이다. 독립 확증에는 다른 날짜·모델·실행 로그가 필요하다.
