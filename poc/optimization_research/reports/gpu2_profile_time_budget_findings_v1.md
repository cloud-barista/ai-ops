# GPU 2개 프로파일 시간 예산의 90분위 학습: 미채택

2026-09-29. **고정 학습기는 미채택이다.** GPU 1개 프로파일 종료 직후 GPU 2개 프로파일의 로컬 소요시간을 예측했을 때, 90분위 히스토그램 부스팅의 encoder 모델 균등 pinball 손실은 가장 강한 기준선 Ridge+잔차 보정의 `0.019802→0.018461초`로 **6.77%** 낮아졌다. 그러나 시험 피복률은 **85.66%**로 고정 하한 88%에 못 미쳤고, 5초 예시 예산에서 실제로 초과할 후보를 가능하다고 판단한 경우가 **5건**으로 허용 4건을 넘었다. 두 게이트가 실패했으므로 이 학습기를 추천·배포 판단에 쓰지 않는다.

## 자료와 결정 의미

원본 SQLite (`/path/to/aiops/runs/khu-hf-local-exhaustive/executions.sqlite3`)의 `execution_measurements` 4,856행 중 GPU 1개/2개 정적 실행이 각각 2,300개다. 고정 Parquet 추출 (`../data/audit_v1/measurements.parquet`)에서 `(model_id, model_revision, workload_group, cpu_core_limit)`로 짝지은 **2,300쌍**은 모두 GPU 1개가 끝난 뒤 GPU 2개를 시작했고, 두 시간은 모두 양수다. Encoder 추론 8모델의 2,048쌍과 동종 donor가 없는 Qwen causal LM 252쌍을 구분했다. 정답 `E₂ = GPU2.finished_at−GPU2.started_at`은 동일 호스트 수집기의 **로컬 프로파일링 실행 구간**이다. VM 생성·이미지 배포 준비시간, 실제 `SCALE_OUT` 명령의 효과, 사용자 지연 SLO가 아니다.

학습은 `2026-09-11 13:00 UTC`까지 **GPU 2개가 완료된** encoder 1,044쌍에서, 시험은 그 뒤 **GPU 1개가 시작한** encoder 1,004쌍에서 했다. 대상 모델을 통째로 donor에서 제외한 8 fold마다 donor 912~916쌍, 시험 124~128쌍이다. Qwen 252쌍에는 숫자를 출력하지 않고 보류한다. 입력은 GPU 1개 측정에서 이미 확인한 `E₁`, 그 행의 내부 wall/load/compute 시간·파라미터 바이트와 사전 batch/length/concurrency/repeats/CPU affinity뿐이다. GPU 2개 행에서 읽는 수치는 짝 검증 시각과 정답 시작·종료시각뿐이다. 대상 GPU 2개 `result_json`, node 계측·처리량·내부 `wall_seconds`를 특징으로 쓰지 않는다. 이 입력 경계는 [규약](gpu2_profile_time_budget_protocol_v1.md)과 [누출 테스트](../tests/test_gpu2_profile_time_budget.py)에 고정했다.

## 동일 시험 행의 비교

손실은 **모델별 0.9 pinball 손실의 8모델 균등 평균**이다. 피복률과 5초 승인/오승인은 1,004개 시험 프로파일 전체에서 셌다. 예측 시간이 실제 시간보다 길 때의 평균 초과분도 함께 기록했다.

| 방법 | 모델 균등 pinball 초 ↓ | 전체 피복률 ↑ | 평균 과대 예산 초 ↓ | 5초 가능 승인 | 그중 실제 5초 초과 ↓ |
| --- | ---: | ---: | ---: | ---: | ---: |
| `E₁ + donor median(Δ)` | 0.046182 | 46.81% | 0.0392 | 770 | 20 |
| `E₁ + donor p90(Δ)` | 0.020365 | **89.14%** | 0.1361 | 720 | **4** |
| `E₁ × donor p90(E₂/E₁)` | 0.919776 | 86.25% | 8.1956 | 197 | 0 |
| Ridge Δ + 훈련 잔차 p90 | **0.019802** | 86.85% | 0.1215 | 735 | 5 |
| 고정 HGB Δ 90분위 | **0.018461** | 85.66% | 0.1102 | 734 | 5 |

표의 `donor p90(Δ)`는 [이전 적격성 감사](replica_profiling_budget_feasibility_2026_09_29.md)가 **같은 1,004쌍**에 보고한 pinball `0.0204초`, 피복 `89.1%`, 오승인 `4건`과 반올림 수준에서 일치한다. 여기서는 원본과 fold를 다시 구축하고 다른 네 방법도 **같은 행**에 채점했다. 비율 기준선의 5초 오승인 0건은 거의 대부분의 후보를 승인하지 않은 결과이며, 평균 과대 예산이 8.20초로 크다. 기준선의 우열은 오승인 한 숫자로 판단하지 않는다.

모델별로 HGB가 최선 p90 기준선 Ridge보다 손실이 낮은 경우는 **6/8모델**이었다. 어떤 모델도 Ridge보다 20%를 넘게 악화되지는 않았다. 그러나 아래처럼 Albert의 피복률은 64.5%이고 RoBERTa도 81.2%여서 전체 안전 한계를 통과하지 못했다. 같은 자료에서 사후 보정 계수나 문턱을 맞춰 다시 채택 시험하지 않는다.

| 대상 모델 | 시험 쌍 | Ridge pinball | HGB pinball | HGB 피복률 | HGB 5초 오승인 |
| --- | ---: | ---: | ---: | ---: | ---: |
| RoBERTa base | 128 | 0.025642 | 0.022930 | 81.2% | 2 |
| Albert base | 124 | 0.023771 | 0.023616 | 64.5% | 2 |
| DistilBERT base | 128 | 0.014259 | 0.014639 | 88.3% | 0 |
| BERT base | 128 | 0.020053 | 0.018750 | 97.7% | 0 |
| BERT L2 H128 | 124 | 0.016511 | 0.012329 | 96.0% | 0 |
| BERT L4 H256 | 124 | 0.021983 | 0.020491 | 87.9% | 1 |
| BERT L4 H512 | 124 | 0.015531 | 0.015899 | 81.5% | 0 |
| Electra small | 124 | 0.020663 | 0.019030 | 87.9% | 0 |

## 고정·재현·해석 한계

평가 전 [규약](gpu2_profile_time_budget_protocol_v1.md)에 특징, 모델, 분할, 다섯 채택 게이트를 기록했다. [동결 manifest](../artifacts/gpu2_profile_time_budget_v1/freeze.json)의 원본 SQLite SHA-256은 `9ba1900183d122c3034ea4702e5f3cb1f29e604511c5fde05e176460ce3fa6f7`, Parquet SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`, 규약 SHA-256은 `bbb246c9de37232aceae7450c456654c6ace9525f8d731bfc3c603ae8a97d307`, 채점 코드 SHA-256은 `a11f12ba4ae5ed4416f2e0efc0bb197708e0272302d221a36275b7421474eb5c`다. 이 manifest를 쓴 뒤 처음 후행 점수를 계산했다. 행별 예측 (`../artifacts/gpu2_profile_time_budget_v1/predictions.csv`)의 SHA-256은 `3de1a90113acd71f245dba136a8df28b7b8c9e15c9c03dcde661f48c48ce4398`, [전체 지표](../artifacts/gpu2_profile_time_budget_v1/metrics.json)에 fold·모델별 값과 라이브러리 버전이 있다.

재현 명령은 연구 루트에서 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/evaluate_gpu2_profile_time_budget.py score`다. 실행 전에 스크립트가 네 입력 SHA와 모델 설정을 동결 manifest와 대조한다. `PYTHONPATH=. pytest -q tests/test_gpu2_profile_time_budget.py`의 5개 테스트가 통과했다. 원본의 GPU 2개 사후 결과 필드를 극단값으로 바꿔도 입력과 정답이 변하지 않는지, 시점이 거꾸로 된 쌍을 거부하는지, 대상 모델과 절단 뒤 donor 라벨을 학습 전에 차단하는지 검사했다.

이 시험의 시간 절단과 과제 자체는 이전에 같은 SQLite의 시험 정답을 일부 확인한 뒤 정했다. 따라서 수치가 게이트를 통과했더라도 새로운 모델·호스트에 대한 독립 검증은 아니다. 프로파일링 예산 예측의 정답은 확실하지만, 경희대의 **AI-App 최소 VM 형상·실제 배포/스케일 조치**의 정답으로 옮길 수 없다. 현재 자료에서는 단순 donor p90 규칙을 오프라인 비교 기준으로 유지하고, 별도 학습기 채택은 보류한다.
