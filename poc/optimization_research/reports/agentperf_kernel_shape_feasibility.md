# AgentPerfBench 커널 프로필의 AI-App 가속기 추천 정답 적합성

작성일: 2026-09-29. 결론은 **AI-App 전체의 GPU 종류·수량 추천 학습에는 NO-GO**다. 이 자료는 단일 커널의 측정 시간을 제공하지만, 앱 전체 지연·처리량·GPU 메모리 피크·배포 결과와 연결하는 실행 ID나 요청별 실행 경로가 없다. 좁게는 이미 연산 구성과 형상이 알려진 *prefill 커널 시간의 GPU별 비교*를 탐색할 수 있으나, 이것을 GPU 필요량이나 사용자 SLO의 정답으로 바꾸면 근거 범위를 넘는다.

## 원본과 재현

- [공식 AgentPerfBench 카드](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/README.md)의 고정 revision은 `722f2b9d8a6acd222811f4e210a96865f774f6ee`다. 카드에 따르면 `kernels_labeled`는 A100·H100·RTX 3090·RTX 2080 Ti에서 수집한 Nsight Compute의 **커널별** 프로필이다.
- 공식 원본 [`kernel_profiles/kernels_labeled.parquet`](https://huggingface.co/datasets/agent-perf-bench/AgentPerfBench/blob/722f2b9d8a6acd222811f4e210a96865f774f6ee/kernel_profiles/kernels_labeled.parquet)만 추가로 내려받았다. 로컬 `data/external/agentperfbench/kernel_profiles/kernels_labeled.parquet`, **657,407바이트**, SHA-256 `f9822c8c872bbfc00bc69d335ebef23bb8603f9a9e624267c56905f1e8cf8aa8`이다. HTTP 응답의 `X-Repo-Commit`도 위 revision이고 원본 ETag가 같은 해시였다. 앞서 받은 카드 SHA-256은 `49ff2f0aad7a8baf39b63458c0a4433a141646d019915c000fc766b992b5cf5e`다.
- 재현: 연구 루트에서 `python scripts/audit_agentperf_kernel_shape_support.py --output artifacts/agentperf_kernel_shape_support_v1/audit.json`. 스크립트는 두 파일의 SHA와 행 수를 먼저 확인한다. [감사 코드](../scripts/audit_agentperf_kernel_shape_support.py), [전체 수치](../artifacts/agentperf_kernel_shape_support_v1/audit.json). 모델은 학습하지 않았다.

## 실제 지원량

원본은 **148,077행·22열**이지만 완전 중복 행이 **85,542개**다. 계층마다 반복되는 같은 커널 호출일 수 있으므로 이를 오류라고 단정하지 않는다. 다만 행을 독립적인 14.8만 학습 사례로 셀 수 없다. `gpu_time_duration_ms`는 모두 양수다. `roofline_sweep`·`misc_sweep`·`flash_sweep` 합계 12,736행은 합성 연산 스윕이고, 실제 모델 이름의 `_prefill` 10개 출처는 135,341행이다.

측정 이후의 `gpu_time_duration_ms`, `dram_bytes_sum`, launch 크기·레지스터 수를 입력에서 제외했다. `kernel_name`도 GPU가 선택한 구현을 드러내므로 사전 GPU 선택 입력으로 쓰지 않았다. 남은 공개 메타데이터 `(source, model, kernel_family, dtype, op_type, M, N, K, bs, seq, n_heads, head_dim, kv_heads, numel)`가 같은 행을 논리 연산 조건으로 묶었다. 이것은 **동일한 실제 호출이 검증된 짝이 아니라 공개 열로 만들 수 있는 비교 후보의 상한**이다. 호출/레이어 ID가 없고 일부 크기 값은 비어 있다.

| 구분 | 논리 연산 조건 | 복수 GPU 조건 | GPU 3종 이상 | GPU 4종 |
|---|---:|---:|---:|---:|
| 실제 모델 prefill 10개 | 1,025 | **660** | 206 | 22 |
| 세 합성 스윕 | 1,195 | 831 | 357 | 0 |

실제 모델 660조건은 **7모델**에만 분포한다. A100–RTX 3090 짝이 650개인 반면 A100–H100은 **2모델에서 69개**, H100–RTX 2080 Ti는 **1모델에서 22개**다. 10개 실제 모델의 `bs`, `seq`에서 관측된 비결측 값은 모두 **배치 1·길이 128뿐**이다. 따라서 길이·배치별 요구량 곡선을 학습할 수 없다. 논리 조건 660개 중 648개에 `M,N,K` 전부 또는 `numel`이 있지만, 조건/GPU 셀 1,913개 중 **421개**는 여러 `kernel_name`을 포함한다. **1,737개**는 여러 커널 행, **1,712개**는 서로 다른 측정 시간을 포함한다. 실행/호출 식별자 없이 어떤 호출과 시간을 다른 GPU의 어느 호출에 대응시킬지 확정할 수 없다.

`kernel_name`까지 동일해야 한다는 민감도 검사에서는 A100–H100의 비교 후보가 **0개**가 된다. GPU별 구현 이름이 달라서 생긴 현상일 수 있으므로 이는 “A100–H100에 같은 논리 연산이 없다”는 뜻이 아니다. 반대로 구현 이름을 무시한 69개도 실제 동일 호출의 성능비라고 보장하지 않는다.

## 독립 검증과 기준선

원본 `held_out=True`는 Llama-3.1-70B, Llama-3.3-70B, Qwen2.5-72B 세 출처에 붙어 있다. 이 중 복수 GPU가 있는 출처는 Llama-3.1-70B와 Qwen2.5-72B의 **33·32 논리 조건**뿐이고 둘 다 A100–RTX 3090이다. 출처/모델 전체를 빼는 평가는 가능하지만 두 보류 모델의 배치·길이는 여전히 1·128이다. H100에는 해당 보류 출처가 없으며, 복수 GPU로 비교할 수 있는 H100 실제 모델은 Llama-3.1-8B와 gpt-oss-20b 두 개다. **새 모델과 새 GPU 종류를 함께 보류**하는 운영 일반화 검증으로 보기에는 지원량이 매우 작다.

훗날 커널 단계 연구를 한다면 같은 논리 조건의 반복 행을 같은 분할에 묶고, 모델 전체 제외를 기본으로 해야 한다. 비교 기준선은 훈련 모델의 GPU·연산 종류별 중위 시간, FLOP/바이트와 공개 GPU 대역폭으로 만든 단순 roofline, 그리고 사전 A100 계측을 허용할 때의 GPU 간 일정 비율 보정이다. GPU를 통째로 보류하면 해당 GPU의 경험적 중위값을 훈련에서 쓸 수 없으므로 물리 사양만 쓰는 기준선과 따로 비교해야 한다. `kernel_name`, NCU 측정 열, 원본 `held_out` 표식을 예측 특징에 넣으면 해석이 무너진다. 이번에는 앱 수준 정답이 없어 해당 기준선이나 학습기를 훈련하지 않았다.

## 경희대 업무와의 연결

이 커널 파일 자체로는 AI-App **요구사항 분석**, CPU·RAM·GPU 수량, 앱 지연/처리량 SLO, 실제 배포·스케일 명령의 결과, 비용을 판단할 수 없다. 커널 연산 구성은 신규 사용자 요청에 자연히 포함되지 않으며, 얻으려면 별도 모델 그래프 분석이나 사전 프로파일이 필요하다. 그러므로 현재 KHU 핵심 알고리즘의 독립 학습 자료로 승격하지 않는다. 이미 확보한 AgentPerfBench `trace_replay`의 앱 서빙 지표와도 원시 커널 행을 임의로 연결하지 않는다. 특정 모델의 실행 ID와 완전한 커널 호출 경로, 같은 조건의 앱 수준 관측이 추가되면 단계별 설명 변수로 재평가할 수 있다.
