# GPU 1→2 복제 프로파일의 CPU 작업량: 첫 학습 결과

2026-09-29. **고정 HGB 학습기는 미채택이다.** encoder 모델 전체 제외 평가에서 총 CPU초의 평균 절대 로그오차를 가장 강한 대조군 Ridge의 `0.05627`에서 `0.04558`로 낮췄지만, 작은 BERT 모델 하나에서는 Ridge보다 **2.13배** 나빴다. [사전 규약](replica_cpu_work_protocol_v1.md)의 모델별 최대 악화 20% 조건을 어겼다. 같은 원본의 그 모델을 보고 특징·초매개변수·게이트를 고쳐 재채점하지 않는다.

## 왜 이 과제를 선택했는가

기존 [물리 실행 SQLite 감사](local_signal_refresh_2026_09_28.md)에는 새 AI-App 배포 명령, 준비된 VM 수, 전후 사용자 SLO 정답이 없다. 다만 GPU1을 먼저 측정하고 같은 프로필을 GPU2의 두 자식 프로세스로 실행한 **2,300개 고유 짝**은 있다. 두 자식의 `cpu_seconds` 합계가 상위 결과와 2,300짝 모두 일치하고, 자식 각각이 GPU1과 같은 전체 예제 수를 수행했다. GPU1 종료는 매번 GPU2 시작보다 빨랐다. 원본 수집기는 GPU1을 장수명 프로세스에서 실행하고 GPU2를 새 자식 프로세스 두 개로 실행한다. `cpu_seconds`는 encoder의 실행 코드 (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py:636`)와 Qwen 실행 코드 (`/path/to/aiops/khu_policy_agent/live_collector.py:1860`)에서 `time.process_time()`으로 측정하며 모델 적재와 추론을 포함한다. 따라서 이 차이는 **정적 복제 벤치마크의 관측 CPU 작업량**이고 GPU 수 증가의 인과 효과나 VM 최소 CPU 요구량이 아니다.

주 캠페인의 GPU1 CPU 시간 중앙값은 `0.604초`, GPU2 자식 합계 중앙값은 `4.432초`다. 2×GPU1은 2,300짝 전부를 과소예측했고 실제/기준 비율 중앙값은 `4.00`이었다. encoder 8모델·2,048짝을 주 시험으로 삼았고, 유일한 causal LM Qwen 252짝은 동종 donor가 없어 학습기를 보류했다. Qwen에서도 2× 기준선은 252짝 전부 과소예측했으나 이 분포로 Qwen 모델 일반화를 주장하지 않는다.

## 동결 방법과 시험

모델 점수 전에 [규약](replica_cpu_work_protocol_v1.md), [코드](../scripts/evaluate_replica_cpu_work.py), 고정 원본 추출의 SHA-256과 적격 짝 수를 [freeze](../artifacts/replica_cpu_work_v1/freeze.json)(SHA-256 `a799729049649d6aaf7160b30426e2615bcee02a6242cecb1c30173b2c123f9e`)에 기록했다. 규약 SHA-256은 `999519c92a9e55b94c11e47ccc7379979a2f0920d392e92f0a323f51aed95c6a`, 코드 SHA-256은 `4f22c0095f5d6997c7161456f30131a6ad00580072cd2c716ec0fb4de964c0fa`다. GPU1 CPU초·적재/추론 시간·모델 바이트·사전 작업 조건만 입력했다. 각 대상 encoder 모델을 통째로 제외하고 다른 7모델의 주 캠페인으로 2×GPU1, donor 중앙 비율, Ridge, HGB를 비교했다. 앞선 GPU1 측정 후 GPU2 결과를 예측하는 시점이며 새 앱 등록 시점의 무계측 추천은 아니다.

| 주 캠페인 방법 | 8모델 균등 평균 절대 로그오차 ↓ | 평균 CPU초 절대오차 ↓ | 과소예측 비율 |
| --- | ---: | ---: | ---: |
| GPU1 CPU초 × 2 | 1.36495 | 3.072초 | 100.0% |
| donor 중앙 비율 | 0.50273 | 3.151초 | 49.5% |
| Ridge 비율 | **0.05627** | 0.281초 | 50.4% |
| HGB 비율 | **0.04558** | 0.207초 | 52.2% |

HGB가 Ridge보다 낮은 오차를 낸 모델은 **8개 중 7개**다. 예외는 `google/bert_uncased_L-2_H-128_A-2`로 HGB `0.12880`, Ridge `0.06043`이었다. 이 모델의 HGB 오차는 해당 모델의 최선 대조군의 `2.13배`이며, 사전 채택 기준인 `≤1.20배`를 위반한다. 나머지 집계 조건인 주 평균 5% 이상 개선, 6모델 이상 개선, 두 별도 입력 캠페인의 최고 대조군 대비 105% 이내는 통과했다. `novel-matrix`의 HGB/Ridge 평균 오차는 `0.04924/0.05426`, `space-filling`은 `0.04612/0.05382`다. 두 캠페인은 **주 캠페인보다 먼저 수집한 같은 호스트·같은 모델**이므로 새로운 모델이나 미래 운영 상황의 독립 시험이 아니다.

이 결과는 GPU1을 계측한 뒤 GPU2 프로파일링을 해야 할 때 CPU 작업량이 입력·모델에 따라 크게 달라진다는 연구 신호를 보여준다. 학습된 HGB는 모델별 실패 조건 때문에 연구용 보조 예측기로도 채택하지 않았다. Ridge는 강한 대조군이지만 사전에 Ridge 채택 게이트를 정의하지 않았으므로 시험 뒤에 운영 모델로 승격하지 않는다. 새 물리 모델·호스트에서 같은 시작 방식과 CPU 시간 측정을 반복하면 그때 모델별 악화와 재현성을 독립 검증할 수 있다.

## 재현 자료

[전체 점수](../artifacts/replica_cpu_work_v1/results.json)(SHA-256 `c6619a31e2feeb8e966b7c5e6004d60ecf6b02aca67131f8ed0b7642fe28dbf1`)와 짝별 예측 (`../artifacts/replica_cpu_work_v1/predictions.csv`)(SHA-256 `35f13affbf724fa86486a65b4839ca6ae057939dae352bbda9405cefaf7bd74d`)에 주 2,048짝과 두 보조 캠페인 224짝을 저장했다. 연구 루트에서 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 scripts/evaluate_replica_cpu_work.py evaluate`로 freeze 일치 검사 후 재실행한다. [집중 테스트](../tests/test_replica_cpu_work.py) 6개는 원본 쌍 수, 자식 합계, 시점, GPU2 정답 누출, freeze 변경 차단, 모델 균등 집계를 확인했다.
