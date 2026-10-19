# IOG RTX 4090 GPU 2→4 상대 반응: 모델 전체 제외 후향 탐색

## 결론

**현재 학습기를 형상 추천 메커니즘으로 채택하지 않는다.** 세 모델의 동일 가중 요청 처리량 MALE는 Ridge `0.0520`, 훈련 모델 평균 `0.0540`, 현재 GPU2 처리량 유지 `0.0617`이었다. 처리량 이득은 훈련 평균 대비 `0.0021` 로그 단위로 작다. 반면 TTFT p95 MALE는 Ridge `1.1548`, 훈련 평균 `0.9077`, 현재 TTFT 유지 `0.6610`으로 Ridge가 세 모델 모두에서 뒤졌다. 처리량·지연을 함께 개선하지 못했다.

이것은 [IOG 공개 결과](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/README.md)의 같은 날 vLLM 측정에 대한 **후향 탐색**이다. 원본을 이미 적합성 감사에서 열람했으므로 블라인드 독립 검증이 아니다. GPU 2→4는 연구자가 최대 가용 GPU를 tensor parallelism으로 사용한 구성이고, VM Scale-out 조치·GPU 복제본 증가와 다르다. KHU SQLite의 저장 모델·라벨을 학습에 넣지 않았다. [채점 전 구현 규약](iog_gpu24_response_protocol.md), [자료 적합성 감사](iog_llm_inference_benchmark_feasibility.md)

## 고정 자료와 평가

고정 commit `4990a807c6f1971edda635c18d98550059782eb8`의 `results/*.json` **120파일·130실행**을 읽고 SHA-256을 검증했다. 같은 RTX 4090의 GPU 2장과 4장, ShareGPT 200요청, 목표 QPS `1/4/8/16/32`를 완비한 **세 모델·15쌍·30셀**만 사용했다. 대상 모델은 DeepSeek-R1-Distill-Llama-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-R1-Distill-Qwen-14B다. 한 모델의 5 QPS를 모두 빼고 다른 두 모델의 10쌍에서 Ridge와 훈련 로그 반응 평균을 적합하는 3분할이다. 세 모델이 모두 DeepSeek-R1-Distill 계열이고 모든 실행은 **2025-06-10** 하루 기록이므로 계열·날짜 홀드아웃은 아니다. QPS 15행을 독립 모델 15개처럼 해석하지 않는다.

입력 특성은 목표 QPS와 GPU2의 요청/초·TTFT p95의 로그뿐이다. Ridge의 α는 `1.0`, 특성 표준화는 각 훈련 두 모델에서만 적합했다. GPU4 결과, 출력 토큰 수, 날짜는 예측 입력에 넣지 않았다. 비교군에는 `R4=R2`·`TTFT4=TTFT2`와 `R4=2R2`·`TTFT4=TTFT2`, 목표 QPS `λ`로 처리량을 제한한 `R4=min(2R2,max(λ,R2))`·`TTFT4=TTFT2`, 훈련 두 모델의 로그 반응 평균이 있다. MALE는 QPS 5개별 `|log(예측값/관측값)|` 평균이고, 전체 값은 모델 세 개를 동일 가중한다.

| 방법 | 요청/초 MALE ↓ | TTFT p95 MALE ↓ |
| --- | ---: | ---: |
| GPU2 값 유지 | 0.0617 | **0.6610** |
| 처리량 2배·TTFT 유지 | 0.6315 | **0.6610** |
| 목표 QPS 제한 처리량·TTFT 유지 | 0.3376 | **0.6610** |
| 훈련 두 모델의 로그 반응 평균 | 0.0540 | 0.9077 |
| 훈련 두 모델의 Ridge | **0.0520** | 1.1548 |

| 제외 모델 | GPU2 값 유지 요청/초·TTFT | 훈련 평균 요청/초·TTFT | Ridge 요청/초·TTFT |
| --- | ---: | ---: | ---: |
| Llama-8B | **0.0367** · **1.0552** | 0.0375 · 1.3240 | 0.0391 · 1.2980 |
| Qwen-7B | 0.0403 · **0.2648** | 0.0397 · 0.3147 | **0.0373** · 0.6592 |
| Qwen-14B | 0.1080 · **0.6630** | 0.0850 · 1.0846 | **0.0796** · 1.5073 |

실측 GPU4/GPU2 처리량 비율은 **1.002–1.243**, 중앙값 `1.035`였고 15쌍에서 감소는 없었다. TTFT p95 비율은 **0.156–9.306**, 중앙값 `1.059`이며 8/15쌍에서 악화됐다. 특히 Llama-8B의 목표 QPS 1에서는 GPU4 TTFT가 GPU2의 `9.31`배였다. 이처럼 처리량의 작은 상대 변화에 비해 지연 반응의 방향·크기가 모델·부하마다 달라, 두 donor 모델의 10쌍만으로 TTFT를 안정적으로 전이하지 못했다. 관측 비교는 실행 시각·서버 구성 차이와 혼재하므로 GPU 수 변화의 인과 효과로 해석하지 않는다.

사전 고정한 출력 토큰 민감도 조건 `|log(출력토큰4/출력토큰2)|≤log(1.05)`에는 **15/15쌍**이 남았다. 모델별 비율 범위는 Llama-8B `1.0000–1.0099`, Qwen-7B `0.9971–1.0036`, Qwen-14B `0.9927–1.0000`이라 부분집합 MALE는 전체와 같다. 이는 이 기준에서 결과가 바뀌지 않았다는 확인일 뿐, 원본 JSON에 없는 개별 요청·프롬프트 ID가 형상 간 동일했다는 증거는 아니다. 선택한 GPU 2/4 셀에는 반복 실행이 없어 날짜별·실행별 변동도 추정할 수 없다. 실제 사용자 SLO·메모리/OOM·CPU 형상·VM 조치 결과도 없다.

## 재현과 무결성

- [동결 manifest](../artifacts/iog_gpu24_response_v1/freeze_manifest.json): 규약 SHA-256 `0f9d4731f0772aafb96e24e7646cc74c6a00db4df4a21e9b65167647cfe7b2d9`, [스크립트](../scripts/evaluate_iog_gpu24_response.py) SHA-256 `e92eab44cd3129ea3a8d1b68fa1645d82e95f8386bea830caee9ca1b9134dff3`, [원본 해시 목록](../artifacts/iog_gpu24_response_v1/source_manifest.json)의 SHA-256 `39931953fd80e625396bdc729a6357f3d3ee20af24caf918ce53c47ffb1c4961`, 원본 ZIP (`../artifacts/iog_gpu24_response_v1/raw_results.zip`)의 SHA-256 `447a8deb234ab687a92d7d40fe3d7b99ab564e4e14b4e8f7cd8c868ec65aa8bc`. 원본 경로·해시 집계 `92ffc3479de653491c7f252c88adfc3ff9c1e231ad23672978b8d3962d32f215`와 182,641 bytes를 확인했다. 원본 저장소의 [Apache-2.0 LICENSE](https://github.com/IOG-Foundation/llm-inference-benchmarks/blob/4990a807c6f1971edda635c18d98550059782eb8/LICENSE)를 [아티팩트 옆에도 보관](../artifacts/iog_gpu24_response_v1/UPSTREAM_LICENSE.txt)했다.
- `/usr/bin/python poc/optimization_research/scripts/evaluate_iog_gpu24_response.py freeze` 후 `/usr/bin/python poc/optimization_research/scripts/evaluate_iog_gpu24_response.py score`로 실행했다. `score`는 동결 파일·스크립트·원본 해시를 다시 확인하고, 같은 결과가 이미 있으면 재채점하지 않는다. 선택 30셀 (`../artifacts/iog_gpu24_response_v1/selected_cells.csv`), 75개 방법별 예측 (`../artifacts/iog_gpu24_response_v1/predictions.csv`), 모델별 지표 (`../artifacts/iog_gpu24_response_v1/per_model_metrics.csv`), [기계 판독 지표](../artifacts/iog_gpu24_response_v1/metrics.json)에 세부값이 있다.
- [집중 검사](../tests/test_iog_gpu24_response.py) 5개가 통과했다. 대상 GPU4 결과를 바꾸어도 대상 예측이 바뀌지 않는 누출 검사, 셀 누락 시 실패, 같은 셀의 가장 이른 실행 선택, 목표 QPS 상한 수식, 출력 길이 민감도 선택을 확인했다. 별도 CSV 재계산에서 75개 예측의 `|log(예측/관측)|`, 모델별·전체 MALE, 목표 QPS 상한 예측이 저장 수치와 일치했다.
