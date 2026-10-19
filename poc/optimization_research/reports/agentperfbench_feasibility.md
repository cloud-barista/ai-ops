# AgentPerfBench 데이터 적합성 감사: LLM 서빙 GPU 형상 추천

> 이 문서는 `trace_replay` 초기 원본 감사 시점의 상태다. 이후 `kernels_labeled`도 받아 [커널별 가속기 정답 적격성](agentperf_kernel_shape_feasibility.md)을 따로 감사했다.

작성일: 2026-09-29. 대상은 경희대 핵심 모듈의 **별도 공개 LLM 서빙 도메인** 실험이다. 기존 KHU/IOG SQLite의 배포 시간·배치 처리량 측정과 합치지 않는다.

## 결론

`trace_replay`는 **관측된 GPU 형상 간 TTFT·TPOT·요청 처리량 예측 및 조건부 형상 선택의 소형 연구 실험**에는 쓸 수 있다. 모델 9개, 형상 14개, 실행 2,932건이 있고, 같은 모델·서빙 엔진·워크로드 프로필·동시성·요청 수에서 성공률 100%와 양의 지표를 가진 **대체 형상 2개 이상**의 비교가 551개 묶음(1,734행)에 남는다. 다만 미관측·실패한 형상을 포함한 전체 탐색 결과가 없으므로 운영용 인프라 추천 정답으로 취급할 수 없다. CPU·RAM·VM 생성/배포·스케일 조치 결과도 없다.

## 원본과 재현

- 공식 [데이터 카드](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/README.md)와 [공식 파일 목록](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/tree/722f2b9d8a6acd222811f4e210a96865f774f6ee)을 확인했다. 고정 revision: `722f2b9d8a6acd222811f4e210a96865f774f6ee`.
- 내려받은 원본: [trace_replay/summary.parquet](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/trace_replay/summary.parquet) 639,880바이트, SHA256 `568efbdae473a830ba792317fb09cfba0ea4f667db67b93d28e72637a5c71d1e`; [synthetic_distributional/summary.parquet](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/synthetic_distributional/summary.parquet) 74,228바이트, SHA256 `db70f78a09cec9dee9159c5011892fb28510e06d2824cc175d26bb47d0e39585`. 로컬 위치: `data/external/agentperfbench/`.
- 카드 `README.md`: 13,873바이트, SHA256 `49ff2f0aad7a8baf39b63458c0a4433a141646d019915c000fc766b992b5cf5e`.
- 재현: 저장소 루트에서 `python3 scripts/audit_agentperfbench_support.py` 실행. 스크립트는 위 세 파일의 SHA를 확인하고 JSON 진단을 출력한다. 이 초기 감사에는 148,077건의 `kernels_labeled`를 사용하지 않았다.

## 실제 데이터와 비교 지원량

카드에 따르면 `trace_replay`는 기록된 에이전트 세션의 입력·출력 길이(ISL/OSL)를 재생한다. 측정은 semaphore 기반 폐회로 동시성, 설정마다 3요청 워밍업이며 지표는 **성공한 요청만**으로 계산된다. 엔진은 vLLM 0.19.0 및 SGLang 0.5.9, 형상은 2080 Ti·3090·A100 40GB·H100의 1/2/4/8 GPU 중 관측된 14종이고, 다중 GPU에서는 텐서 병렬화를 쓴다. [공식 방법 및 스키마](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/README.md#benchmark-methodology)

직접 읽은 parquet는 30열이며 9모델·4모델 계열·2엔진·17프로필·동시성 `{1,5,10,20,40,80}`을 포함한다. `(model, engine, profile, concurrency, hardware)`와 `run_id`는 각각 2,932개로 중복이 없다. `tensor_parallelism`은 2,932행 모두 하드웨어 GPU 수와 일치한다. 정답 지표에는 `p90/p99_ttft_ms`, `p90/p99_tpot_ms`, `p90/p99_e2el_ms`, `request_throughput` 및 토큰 처리량이 있다. 가격·실제 사용자 SLO·CPU/RAM 크기·VM 수명주기·배포/스케일 조치 결과는 열에 없다.

| 필터 및 동일 조건 키 | 대상 행 | 조건 묶음 | 2형상 이상 묶음 | 그 안의 행 | 최대 형상 수 |
|---|---:|---:|---:|---:|---:|
| 원본: 모델·엔진·프로필·동시성 | 2,932 | 1,046 | 633 | 2,519 | 13 |
| 지표 양수 + 성공률 100%: 위 4개 키 | 2,172 | 731 | 442 | 1,883 | 13 |
| 지표 양수 + 성공률 100%: 위 4개 키 + **동일 요청 수** | 2,172 | 989 | **551** | **1,734** | 11 |

마지막 줄의 551묶음은 3형상 이상 177개, 4형상 이상 106개다. 모델별 2형상 이상 묶음은 Llama-3.1-8B 152, Qwen3.5-9B 129, gpt-oss-20b 61, Llama-3.1-70B 60, Llama-3.3-70B 46, Qwen2.5-72B 43, Mixtral-8x7B 30, Qwen3.5-27B 24, gpt-oss-120b 6개다. **14개 전체 형상을 가진 묶음은 없다.** 형상 관측 범위가 모델마다 다른데, 예를 들어 gpt-oss-120b는 2형상/1엔진만 관측된다.

## 품질과 선택 편향

- 카드의 공개 규칙은 성공 요청이 75% 미만인 설정을 제외한다. 이 parquet에는 `successful_requests`나 `failed_requests` 열이 없다. 그러나 `request_throughput × duration_s`가 전 2,932행에서 정수와 최대 `9.1×10⁻¹³` 차이라 성공 건수를 역산할 수 있고, `num_requests`는 카드상 총 요청 건수다. 역산 결과 724행에서 일부 실패, 보존된 행의 최저 성공률은 75.0134%다. 이는 카드의 필터와 일치하지만, **제외된 원래 실행 수와 실패 원인은 알 수 없다.** 실패 형상을 음성 학습 표본으로 만들 수 없다.
- 36행은 TTFT·TPOT와 입력·출력 토큰 처리량이 모두 0인데 요청 처리량은 양수다. Llama-3.1-70B·Llama-3.3-70B·Qwen2.5-72B의 `H100x2/sglang` 특정 세 프로필에서 반복된다. 원인 자료가 없으므로 지연/토큰율 학습에서는 제외해야 한다. 이 36행은 역산상 모두 성공률 100%여서 성공률 필터만으로 제거되지 않는다.
- 같은 5개 키, 양의 지표, 성공률 100%의 551묶음 중 `input_token_throughput × duration_s`로 복원한 총 입력 토큰 수가 형상 간 0.001% 이내인 묶음은 538개다. 반면 총 출력 토큰 수는 1% 이내 297개, 5% 이내 494개이고 최대/최소 비율이 최대 1.526배다. 같은 프로필·요청 수에서도 실현 출력량이 달라 처리량·TPOT 비교에 교란이 있다. 출력량으로 본 실험 집단을 선택하면 결과를 보고 표본을 고르는 편향이 생길 수 있으므로, 사전 정의된 주 분석과 별도 민감도 분석으로 다뤄야 한다.
- 각 조건·형상에 반복 측정이 없고 p99는 표본 수에 민감하다. 보존된 실행만으로 미관측 형상이 메모리 부족인지, 장애인지, 단순 미실행인지 구별할 수 없다. 폐회로 동시성 결과를 실제 유입률 기반 운영 SLO로 바로 옮길 수 없다.
- `synthetic_distributional`의 265행은 같은 프로필에서 적합한 분포로 만든 근사 실행이며 프로필 5개, 동시성 200/320이다. 공식 카드도 직접 운영 재생이 아니라고 밝힌다. `trace_replay`의 **독립 검증 세트로 사용하지 않는다.**

## 최소 실험 설계와 게이트

1. `trace_replay`만 사용하고, 위 6개 지표 양수·역산 성공률 100%를 품질 필터로 둔다. 형상 비교 단위는 `(model, engine, profile, concurrency, num_requests)`로 고정한다. `num_requests`는 비교 집단을 맞추는 데만 쓰며 배포 전 알 수 없는 실행 결과나 측정 지표를 입력 특징으로 넣지 않는다. 입력 토큰 총량 불일치 13묶음 제외 결과를 민감도 분석으로 별도 보고한다.
2. 우선 타깃은 `p90_ttft_ms`, `p90_tpot_ms`, `request_throughput`의 로그 회귀와 **관측된 형상 사이** 조건부 순위다. 모델 이름 자체 암기 대신 공식 카드의 파라미터 규모·계열·정밀도, 워크로드 프로필, 동시성, 엔진, GPU 종류/수/VRAM을 사전 입력으로 쓴다. 카드에 없는 가격·CPU·RAM을 추정 라벨로 만들지 않는다.
3. 주 검증은 모델 전체를 분리한 leave-one-model-out(9 fold)이며 모델별 오차와 macro 평균을 함께 보고한다. 동일 모델의 다른 프로필·형상이 학습과 테스트 양쪽에 걸치지 않게 한다. 모델 계열 전체 보류(4계열) 및 프로필 보류는 지원량을 확인한 보조 일반화 실험이다. gpt-oss-120b처럼 6묶음뿐인 fold에는 불확실성을 명시한다.
4. 평균/중앙값, GPU별 lookup, 물리 사양 기반 단순 회귀 등 강한 기준선과 비교한다. 목표 지연·처리량은 **훈련 fold만으로 미리 고정한** 제한값을 쓰며, 테스트 결과로 정하지 않는다. 형상 선택에서는 오승인율, 가능한 형상 누락, 보류율, GPU 수 초과분을 함께 보고한다. 가격이 없으므로 비용 최적화라 부르지 않는다.
5. 모델별 성능과 보류율이 기준선보다 낫고, 성공 요청만 남기는 선택 편향·출력량 편차에 대한 민감도에서 결론이 유지될 때만 연구 시제품 후보로 둔다. 이 자료만으로 사용자 요구사항 추출, 실제 VM 배포/오토스케일 판단, 운영 SLO 충족 보증은 통과하지 않는다.
