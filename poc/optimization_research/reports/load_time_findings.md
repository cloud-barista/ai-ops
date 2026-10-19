# 로컬 AI 모델 적재 오버헤드 예측: SQLite 학습 결과

## 결론

기존 SQLite에는 응용의 **모델·토크나이저를 로컬 캐시에서 읽는 시간**인 `load_seconds`가 기록되어 있다. 이를 경희대의 응용 요구 분석과 배포·제어 추론 최적화에 필요한 *초기화 비용 신호*로 분리해 학습했다. 모델 전체 제외 평가에서, 이전 모델별 적재시간 중앙값을 모델 크기로 보간한 ExtraTrees가 주 캠페인의 평균 절대 로그오차(MALE)를 전체 donor 중앙값 `0.522`에서 `0.391`로 낮췄다. 같은 모델들의 새 입력 조건에서도 `0.503→0.388`, `0.493→0.363`이었다. 이 결과를 보고 추가한 크기만의 ExtraTrees 비교는 **사후 탐색**이다. 독립 검증이나 학술적 신규성을 주장하지 않는다.

가장 큰 한계는 새 모델 종류다. 유일한 causal LM인 Qwen을 통째로 제외하면 크기만의 ExtraTrees도 주 캠페인 MALE `1.559`였고, 모델 적재시간 중앙값 `1.214초`에 대해 약 `0.255초`를 예측했다. 이 자료에는 Qwen의 작업 종류를 학습시킬 다른 causal LM이 없다. BERT 계열을 함께 제외하면 정적 특징 Ridge가 크기만의 ExtraTrees보다 일관되게 좋았다. 따라서 단일 추천 알고리즘이 새 AI 모델 종류에서도 정확하다고 볼 근거는 없다.

## 라벨과 입력의 의미

- 원본은 고정된 SQLite 추출 스냅샷 (`../data/audit_v1/measurements.parquet`)이다. 같은 `icns` 호스트, `khu-hf-direct-collector/v2`, CUDA 1개 replica, CPU affinity 2코어만 선택했다. 주 캠페인은 **9모델·575실행**, 추가 입력 캠페인은 각각 **9모델·18/44실행**이다. 주 캠페인의 `load_seconds / wall_seconds` 중앙값은 `0.331`이어서, 이 짧은 벤치마크 실행에서는 적재 시간이 무시할 만하지 않았다. 모든 선택 실행은 성공·분석 적격이며, 모델·입력 조건 중복은 없다.
- `load_seconds`는 Hugging Face `from_pretrained`로 모델과 토크나이저를 읽는 구간이다. 분류/encoder 계측 (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py:279`), Qwen 계측 (`/path/to/aiops/khu_policy_agent/live_collector.py:1756`). GPU로 옮기는 `model.to(...)`, 프로세스 생성, VM 생성, 배포 완료를 포함하지 않는다. 따라서 **VM 부팅 시간이나 배포 준비 시간의 정답으로 해석하지 않는다.** 캐시 상태의 명시적 cold/warm 라벨도 없다.
- 입력은 모델 파라미터 바이트, 배치 크기, 최대 입력 길이, 요청 동시성, 작업 종류뿐이다. 파라미터 바이트는 역사적 실행의 `result_json`에서 추출했지만, 추론 시에는 응용 등록 단계에서 같은 메타데이터를 먼저 제공한다는 **가정**이 필요하다. 후보 실행의 적재시간, 처리량, 지연, 실행 중 계측값은 입력하지 않았다.

## 학습·평가 설계

주 캠페인에서 모델 하나를 통째로 제외한 9개 분할로 학습했다. 그 제외 모델의 주 조건과 두 추가 캠페인 조건에 같은 학습기를 적용했다. 추가 캠페인은 **같은 9모델의 다른 요청 조건**이므로 독립적인 신규 모델 집단이 아니다. 수집 시각도 novel·space가 주 캠페인보다 앞서 있어, 이 평가는 시간상 미래 예측 검증이 아닌 **입력 조건 홀드아웃**이다. BERT 네 모델 공동 제외와 BERT+DistilBERT 다섯 모델 공동 제외도 따로 계산했다. 모든 분할의 학습 행은 주 캠페인의 다른 모델만 포함한다. 같은 `model_id`와 revision이 학습·평가에 섞이지 않도록 검사했다.

비교한 방법은 donor 모델별 적재 중앙값의 중앙값, 모델 크기와 적재 중앙값의 비음수 선형식(`고정비용 + 초/GiB × 크기`), 크기만의 로그 Ridge, 크기만의 ExtraTrees, 정적 특징 전체를 쓰는 로그 Ridge·ExtraTrees다. 크기만의 모델은 donor **모델당 중앙값 한 개**로 학습해 64개 요청 조건을 독립 모델 표본처럼 취급하지 않았다. 전체 정적 특징 모델은 실행별 로그 적재시간을 학습했다. Ridge/ExtraTrees 하이퍼파라미터와 분할은 [재현 코드](../scripts/evaluate_load_time.py)에 고정했다. MALE는 `|log(예측초/실측초)|`이고, 모델별 평균을 다시 동일 가중 평균했다. 절대초 MAE도 함께 저장했다.

| 모델 전체 제외, 모델별 동일 가중 MALE | 주 조건 575개 | 새 novel 조건 18개 | 새 space 조건 44개 |
| --- | ---: | ---: | ---: |
| donor 모델 중앙값 | 0.522 | 0.503 | 0.493 |
| 크기 비음수 선형식 | 1.108 | 1.127 | 1.083 |
| 크기 로그 Ridge | 0.503 | 0.512 | 0.468 |
| **크기만의 ExtraTrees** | **0.391** | **0.388** | **0.363** |
| 전체 정적 특징 로그 Ridge | 0.490 | 0.494 | 0.461 |
| 전체 정적 특징 로그 ExtraTrees | 0.446 | 0.428 | 0.404 |

크기만의 ExtraTrees의 절대초 MAE도 위 순서에서 `0.141/0.119/0.124초`로, donor 중앙값의 `0.163/0.138/0.146초`보다 낮았다. 반면 단조 증가하는 크기 비음수 선형식은 모든 캠페인에서 나빴다. 예를 들어 주 캠페인의 DistilBERT는 파라미터 `0.249GiB`에 적재 중앙값 `0.119초`, 더 작은 ELECTRA는 `0.050GiB`에 `0.198초`였다. 이 자료에서는 크기만으로 일정한 적재 대역폭을 가정하기 어렵다.

9모델을 단위로 다시 표집한 탐색적 구간에서 크기만의 ExtraTrees와 donor 중앙값의 주 조건 MALE 차이는 `−0.131`이고 95% 분위 범위는 `−0.283~+0.011`이었다. 9개 중 8개 모델에서 개선했지만 이 구간은 0을 포함한다. 같은 ExtraTrees와 전체 정적 특징 Ridge의 차이는 `−0.098`(`−0.180~−0.021`)이었다. 방법을 같은 SQLite에서 탐색했으므로 이 범위를 사전 지정된 확증적 신뢰구간으로 해석하지 않는다. 모델별 오차 (`../artifacts/load_time_v1/per_model_metrics.csv`), 짝지은 재표본 계산 (`../artifacts/load_time_v1/paired_model_bootstrap.csv`)

## 모델 계열 제외와 적용 경계

| 주 조건, 모델별 동일 가중 MALE | donor 중앙값 | 크기만의 ExtraTrees | 전체 정적 특징 Ridge |
| --- | ---: | ---: | ---: |
| BERT 4모델 공동 제외 | 0.410 | 0.278 | **0.231** |
| BERT+DistilBERT 5모델 공동 제외 | 0.574 | 0.417 | **0.394** |

새 입력 조건에서도 BERT 4모델 공동 제외는 Ridge `0.284/0.211`, 크기만의 ExtraTrees `0.348/0.270`이었다. 다섯 모델 공동 제외에서는 Ridge `0.431/0.364`, 크기만의 ExtraTrees `0.451/0.392`였다. 주 캠페인 하나씩 제외 평균만 보고 크기만의 모델을 범용 기본값으로 확정하면 이 계열 변화가 가려진다. Qwen처럼 학습 donor에 같은 작업 종류가 없는 경우는 **지원 범위 밖**으로 표시해야 한다.

주 캠페인 전체 575행으로 로그 Ridge와 전체 정적 특징 ExtraTrees를 각각 다시 학습해 저장했다. 크기만의 ExtraTrees도 9모델의 적재시간 중앙값 9개로 전체 재학습했다. 저장 모델 셋은 재현·후속 실험용이며, 위 모델 제외 점수가 전체 재학습 모델의 독립 신규 모델 검증이라는 뜻은 아니다. 크기만의 모델 (`../artifacts/load_time_v1/size_only_extra_trees_all_main.joblib`), 전체 특징 Ridge (`../artifacts/load_time_v1/static_log_ridge_all_main.joblib`), 전체 특징 ExtraTrees (`../artifacts/load_time_v1/static_log_extra_trees_all_main.joblib`). 각 모델의 입력 특징 순서, 학습 실행 ID, 적용 범위는 bundle에 있고, 파일·입력·코드 해시는 [manifest](../artifacts/load_time_v1/manifest.json)에 있다.

현재 자료에서 적절한 결론은 **같은 호스트·수집기에서 알려진 계열의 로컬 모델 적재 오버헤드를 예측하는 탐색 모델은 만들 수 있다**는 것이다. 실제 배포·업데이트·Scale-out 결정에 필요한 VM 시작 지연, 이미지 다운로드, 모델 전송, 서비스 준비, 트래픽 시간 변화는 이 라벨에 없다. 이 모델을 운영 `DEPLOY`/`SCALE_OUT` 판단기로 연결하거나 최소 VM 사양의 근거로 쓰지 않는다.

재현:

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_load_time.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_load_time.py' -q
```

예측별 원자료 (`../artifacts/load_time_v1/predictions.csv`), 집계 점수 (`../artifacts/load_time_v1/aggregate_metrics.csv`), [모델·캠페인 분할](../artifacts/load_time_v1/split_manifest.json), [누출·저장 검증 테스트](../tests/test_load_time.py).
