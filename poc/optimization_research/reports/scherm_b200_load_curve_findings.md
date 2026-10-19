# B200 TP2 저부하 세 점에서 고부하 성능 곡선 예측

## 결론

**판정: `EXPLORATORY_NO_GO`.** [Scherm 공개 벤치마크](../data/external/scherm_onprem_2026/SOURCE.md)의 B200·Qwen2.5 AWQ·TP2에서 7모델 모두 동시성 **1·4·8**의 측정을 입력으로 주고 **16·32·64·128·256·512**의 42셀을 모델 전체 제외로 예측했다. 앵커 조건부 Ridge는 다른 6모델의 동시성별 중앙 비율보다 출력 토큰/초 평균 절대 로그오차(MALE)를 **0.25188→0.13422**, TTFT 집계값을 **0.29723→0.27010**으로 줄였다. 그러나 두 지표를 함께 이긴 모델은 **3/7**이고, 가장 나쁜 모델·지표는 단순 기준선의 **2.73배** 오차였다. [점수 전 탐색 규약](scherm_b200_load_curve_protocol.md)의 모델별 안정성 두 조건을 통과하지 못해 고부하 용량 판단기로 채택하지 않는다.

## 정보 경계와 비교

핀 고정 CSV (`../data/external/scherm_onprem_2026/serving_ALL.csv`)는 528집계행이다. `_tp2_` shard로 한정한 Qwen2.5 AWQ 64행·7모델은 모든 비교 동시성에서 중복 없이 성공 반복 `n_ok=10`과 양의 출력률·TTFT를 가진다. `gpu_count=8`은 노드에서 보이는 GPU 수이므로 TP2 사용 수를 그 열에서 읽지 않았다. 모델 제외 fold에서 훈련 모델의 고부하 정답만 Ridge와 donor 중앙 비율 계산에 썼다. 제외 모델의 1·4·8 측정은 모든 방법에 같은 입력으로 주었다. 제외 모델의 16 이상 결과는 점수에만 사용했다.

| 방법 | 출력 토큰/초 모델 동일 MALE ↓ | TTFT 집계값 모델 동일 MALE ↓ |
| --- | ---: | ---: |
| 동시성 8 값 유지 | 1.30318 | 1.82443 |
| 출력률 선형 증가·TTFT 유지 | 1.12283 | 1.82443 |
| 다른 모델의 동시성별 중앙 비율 | **0.25188** | **0.29723** |
| 해당 모델 4→8 기울기 외삽 | 0.97100 | 0.88431 |
| 앵커 조건부 Ridge | **0.13422** | **0.27010** |

Ridge의 평균 개선은 출력률 **46.7%**, TTFT **9.1%**다. 하지만 Qwen2.5-Coder-14B의 출력률 오차는 donor 중앙 비율 `0.040→0.109`로 **2.73배** 커졌다. Qwen2.5-14B의 TTFT는 `0.113→0.215`, Qwen2.5-32B는 `0.169→0.223`으로 악화됐다. 72B, 7B, Coder-7B 세 모델에서만 두 지표가 동시에 개선됐다. 따라서 평균 이득만 보고 모델별 SLO 판단에 쓰면 안 된다.

## 추가 공개 구간의 적격성

같은 통합 CSV에 있는 RTX PRO 6000 Blackwell의 기존 Qwen2.5 AWQ 7모델×7동시성 49행은 출력 토큰/초가 양수이지만 **`n_ok`, `ttft_ms_p95`, `ttft_ms_median`, GPU 수와 VRAM 열이 전부 비어 있다.** [원본 RTX 파일 예시](https://huggingface.co/datasets/Scherm-AI/onprem-llm-benchmark/blob/e19edbaccec8add876073a3b0668e84115a59d0f/raw/rtx-6000/serving_NVIDIA_RTX_PRO_6000_Qwen_Qwen2.5-14B-Instruct-AWQ.csv)는 처리율·TTFT 단일 열을 담지만 반복 성공 수·계측 정의가 B200의 집계 열과 같다는 근거가 없다. 데이터 카드도 GPU별 엔진·실험 환경이 섞임을 설명한다. 따라서 이 구간을 **동일 지표의 독립 고부하 검증**으로 소급하지 않는다. 출력률만의 별도 탐색은 가능하나 공동 용량·지연 채택 판정에는 부족하다.

## 재현성과 해석

[평가 코드](../scripts/evaluate_scherm_b200_load_curve.py)는 출처 SHA-256, 표본 수, 완전한 앵커·고부하 격자, 반복 성공 수, 양의 결과를 확인한다. 행별 예측 (`../artifacts/scherm_b200_load_curve_v1/predictions.csv`), 선택 셀 (`../artifacts/scherm_b200_load_curve_v1/selected_cells.csv`), [모델별 점수·게이트](../artifacts/scherm_b200_load_curve_v1/summary.json)를 저장했다. 관련 [두 테스트](../tests/test_evaluate_scherm_b200_load_curve.py)는 소스 지원량과, 제외 모델의 고부하 정답을 바꿔도 그 모델의 예측이 달라지지 않음을 검증한다.

이 분석은 결과의 일부와 같은 출처의 이전 TP2→TP4 결과를 본 뒤 설계했으므로 **탐색 평가**다. `ttft_ms_p95`는 개별 요청 p95가 아니라 반복별 평균 TTFT의 p95다. 서버가 이미 준비된 warm 추론이며 실제 사용자 SLO, VM 준비시간, 가격·CSP, 스케일 조치·그 효과는 기록되지 않았다. B200 TP2의 7개 동종 Qwen 모델 결과를 KHU SQLite 행과 합치거나 VM 수 추천의 근거로 쓰지 않는다.
