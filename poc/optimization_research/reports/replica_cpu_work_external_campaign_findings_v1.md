# GPU 복제 CPU 작업량: 별도 로컬 캠페인 시험 결과

2026-09-29. **HGB의 사전 안정성 게이트가 다시 실패했다.** `live` 반복 실행에서는 모델 균등 평균 절대 로그오차가 HGB `0.03930`, Ridge `0.08505`였지만, CPU core 조건을 바꾼 `diverse-cpu-matrix`의 작은 BERT 모델에서는 HGB `0.08058`, Ridge `0.06104`로 **1.32배** 나빴다. 사전 모델별 허용 상한 `1.20배`를 넘었다. 앞선 [주 평가](replica_cpu_work_findings_v1.md)의 같은 모델도 HGB가 Ridge보다 `2.13배` 나빴으므로 평균 이득만으로 학습기를 채택하지 않는다.

## 자료·분할의 의미

[사전 규약](replica_cpu_work_external_campaign_protocol_v1.md)은 기존 8 encoder 모델·2,048짝의 주 캠페인을 donor 학습으로 고정하고, 점수를 보기 전 다른 두 수집 캠페인의 짝 검증과 판정 조건을 정했다. 모델별로 대상 모델을 donor에서 완전히 제외하고 기존 [CPU 작업량 v1 코드](../scripts/evaluate_replica_cpu_work.py)의 특징·Ridge·HGB 설정을 그대로 적용했다. `live`의 550 encoder 짝은 **5모델에서 같은 작업 조건을 110회씩 반복**한 것이고, `diverse`의 40짝은 같은 5모델에서 CPU core 4설정×2회다. 같은 모델·GPU·호스트의 **주 캠페인 이전** 수집물이므로 신규 모델/호스트나 시간순 미래 검증이 아니다.

짝에는 `run_id`를 포함했다. GPU1만 있는 `live` 440그룹과 `diverse` 32그룹은 GPU2 정답이 없어 평가에서 제외했다. `live`의 Qwen 1짝은 GPU1 입력 인코딩 `unspecified`, GPU2 `tokenizer_text`여서 엄격한 짝 검사에서 제외했다. 나머지 `live` 659짝과 `diverse` 48짝은 GPU1 선행 시각, 동일 입력, 자식별 전체 예제 수와 CPU초 합계를 통과했다. Qwen 유효 109+8짝은 유일한 로컬 causal LM이라 학습기 `ABSTAIN`이며, encoder **550+40짝**만 점수에 들어갔다.

| 시험 캠페인 | HGB 모델 균등 평균 절대 로그오차 ↓ | Ridge ↓ | HGB/Ridge | 가장 나쁜 모델의 HGB/Ridge |
| --- | ---: | ---: | ---: | ---: |
| `live`, 5모델×110회 | 0.03930 | 0.08505 | 0.462 | 0.871 |
| `diverse-cpu-matrix`, 5모델×8회 | 0.03904 | 0.06197 | 0.630 | **1.320** |

`live`의 수집기 v1에서는 HGB/Ridge가 `0.03864/0.09801`, v2에서는 `0.04019/0.06768`이다. 이는 같은 다섯 모델의 반복이므로 표본 수가 많아도 독립 응용 일반화의 근거가 아니다. 사전 조건 중 두 캠페인의 평균 ≥5% 개선, `live`의 모델별 ≤120% 및 수집기별 비악화는 통과했다. `diverse`의 모델별 ≤120%가 실패했다. 결과를 본 뒤 작은 BERT만 따로 처리하거나 문턱을 바꾸는 새 점수는 독립 확인이 될 수 없다.

## 재현과 다음 판단

[시험 코드](../scripts/evaluate_replica_cpu_work_external_campaigns.py)의 `freeze`를 점수 전에 실행해 원본·주 실험 freeze·두 코드·규약 해시와 짝 수를 [동결 파일](../artifacts/replica_cpu_work_external_campaign_v1/freeze.json)(SHA-256 `4821b1a628e896cd3fe93fc6e60d58c8ee30c81ef9ce6b81996bb4687f2eae18`)에 기록했다. [전체 점수](../artifacts/replica_cpu_work_external_campaign_v1/results.json)(SHA-256 `60e9514744ec9eddda8d1252206b1ad4ca47e8ae012b18cd30087770be7ba652`)와 짝별 예측 (`../artifacts/replica_cpu_work_external_campaign_v1/predictions.csv`)(SHA-256 `467ee1e7419df1c3164ef1e49f517c1a506f1ad01f8cacce2f18dfe2baede6e1`)을 남겼다. `python3 -m pytest tests/test_replica_cpu_work_external_campaigns.py tests/test_replica_cpu_work.py -q`에서 9개 테스트가 통과했다.

다음 모델 개정은 이 점수에 맞춘 같은 SQLite의 재평가가 아니라 새 물리 AI 모델이나 호스트의 독립 측정에서 결정해야 한다. 현재 수치는 두 독립 GPU 자식 프로세스의 **정적 벤치마크 CPU초**이며 실제 VM 생성, CPU 코어 적합성, 사용자 SLO 또는 스케일 조치의 인과 효과를 나타내지 않는다.
