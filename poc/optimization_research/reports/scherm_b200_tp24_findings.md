# Scherm B200 온라인 추론의 TP2→TP4 성능 전이

## 결론

**판정: `EXPLORATORY_NO_GO`.** 새 [Scherm 공개 서빙 벤치마크](../data/external/scherm_onprem_2026/SOURCE.md)에서 B200·Qwen2.5 AWQ의 TP2/TP4가 같은 모델·동시성으로 짝지어지는 **64쌍·7모델**을 확보했다. TP2 실측과 모델 크기·동시성으로 TP4를 예측한 Ridge의 모델 동일 가중 평균 절대 로그오차(MALE)는 출력 토큰/초에서 donor 동시성별 중앙 비율 **0.07159→0.06526**으로 낮았다. 그러나 TTFT에서는 **0.17745→0.17625**로 0.7%만 낮았고, 두 지표가 함께 나아진 모델은 **2/7개**뿐이었다. 사전 기록한 세 탐색 채택 조건 모두 실패해 이 학습기를 형상 추천에 사용하지 않는다.

## 자료와 측정 단위

고정 Hugging Face revision `e19edbaccec8add876073a3b0668e84115a59d0f`의 `data/serving_ALL.csv` SHA-256은 `d7f677cf0c4ccfd8c702c8458c4e6c3d324e90292bf13e5f09e065431e8d5f9e`다. 전체 528개 집계행·14모델·7 GPU 라벨 중 B200 Qwen2.5 AWQ TP2/TP4에서 성공 반복 `n_ok=10`, 양의 출력률·TTFT가 모두 있는 키만 사용했다. 키는 `(model, concurrency)`이며 중복 TP 셀은 없었다. 원본의 `gpu_count`는 노드에서 보이는 GPU 수로 TP2/TP4 행 모두 흔히 `8`이므로 형상 정답에 쓰지 않았다. B200 실행 스크립트 (`../data/external/scherm_onprem_2026/source_scripts/job_serving_b200.sbatch`)의 `--tensor-parallel-size`와 `_tpN_` 파일명으로 TP를 정했다.

서빙 수집기 (`../data/external/scherm_onprem_2026/source_scripts/run_serving.py`)는 임의 입력 길이 512·출력 길이 256의 warm vLLM 시험을 반복한다. `output_tok_s_median`은 반복별 출력 토큰/초의 중앙값이다. CSV의 `ttft_ms_p95`는 **반복별 평균 TTFT의 95분위**이며 개별 사용자 요청의 p95 TTFT가 아니다. 실제 각 실행의 프롬프트 ID·요청별 지연, VM 발행/Ready, 사용자 SLO는 이 집계 CSV에 없다. 공개 스크립트의 실행 설정은 확인했지만 집계행만으로 모든 실행 플래그를 독립 확인할 수는 없다.

## 모델 전체 제외 비교

TP4 정답인 두 로그 비율 `log(TP4/TP2)`을 대상 모델 전체 제외로 학습했다. 모든 방법에 해당 모델·동시성의 TP2 두 실측값을 똑같이 제공한다. 기준선은 TP2 유지, 출력률만 2배·TTFT 유지, 다른 모델의 같은 동시성별 로그 비율 중앙값이다. Ridge는 훈련 fold에서만 표준화하고 고정 `alpha=10`을 썼다. 특징과 채택 문턱은 [점수 전 탐색 규약](scherm_b200_tp24_protocol.md)에 기록했다. 다만 규약 작성 전 자료의 범위와 일부 결과값을 확인했으므로 이 평가는 맹검 검증이 아니다.

| 방법 | 출력률 모델 동일 MALE ↓ | TTFT 반복 평균 p95 모델 동일 MALE ↓ |
| --- | ---: | ---: |
| TP2 유지 | 0.19844 | 0.52132 |
| 출력률 2배·TTFT 유지 | 0.51143 | 0.52132 |
| **donor 동시성 중앙 비율** | **0.07159** | **0.17745** |
| Ridge | **0.06526** | **0.17625** |

Ridge의 출력률 MALE은 최선 단순 기준선보다 약 **8.8%** 낮지만 TTFT 개선은 **0.7%**로 사전 5% 조건에 못 미친다. 개별 모델에서는 Qwen2.5-14B의 TTFT MALE이 donor 중앙값 `0.0552→0.1268`로 **2.30배 악화**했다. 두 목표를 함께 이긴 모델은 72B와 7B 두 개다. 64쌍 중 TP4 출력률은 TP2보다 **44쌍 높고 20쌍 낮았으며**, TTFT 집계값은 **64쌍 모두 낮았다**. GPU 수에 비례한 출력률 2배 가정이 약한 이유를 이 표본에서 확인할 수 있다. 이 패턴을 다른 장치·엔진·모델의 법칙으로 확대하지 않는다.

## 검증과 다음 판단

[평가 코드](../scripts/evaluate_scherm_b200_tp24.py)는 원본 SHA, 64쌍·7모델, 중복·성공·양의 지표 조건을 검사한다. 행별 예측 (`../artifacts/scherm_b200_tp24_v1/predictions.csv`), 선택한 물리 쌍 (`../artifacts/scherm_b200_tp24_v1/selected_pairs.csv`), [전체 결과](../artifacts/scherm_b200_tp24_v1/summary.json)을 저장했다. 결과를 다시 실행해 파일 바이트가 일치했고, [2개 경계 시험](../tests/test_evaluate_scherm_b200_tp24.py)에서 제외 모델의 TP4 정답을 변경해도 그 모델의 예측이 바뀌지 않음을 확인했다.

같은 Qwen2.5 계열·단일 B200 장비에서의 **tensor parallelism 성능 반응**만 평가했다. 원본은 성공한 warm 실행 위주이며 다른 형상의 실패·OOM을 완전하게 기록한 지도 라벨이 아니다. TP4의 성능 이점만으로 필요한 GPU 수, VM 노드 수 또는 최저 비용을 정할 수 없다. KHU SQLite의 배치 replica 성능·모델과 합치지 않았으며 실제 `SCALE_OUT` 효과로 해석하지 않는다.
