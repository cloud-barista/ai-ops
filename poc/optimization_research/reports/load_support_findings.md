# 로컬 모델 적재시간: 지원 범위 확인 후 예측하는 실험

## 결론

기존 SQLite에서 학습한 모델 적재시간 예측기에 **작업 종류별 학습 모델 수를 확인하는 게이트**를 추가했다. 처음 보는 모델을 예측할 때 같은 작업 종류의 서로 다른 학습 모델이 2개 미만이면 적재시간 수치를 내지 않는다. 모델 전체 제외 평가에서 유일한 causal LM인 Qwen은 이 조건에 걸려 보류되었고, encoder 계열 8/9모델에는 답했다. 답한 8모델의 주 캠페인 모델별 동일 가중 평균 절대 로그오차(MALE)는 **0.246**, 절대초 오차는 **0.039초**였다.

이 개선은 **지원 범위를 표시하고 근거 부족한 예측을 막는 것**이다. 답한 요청의 점추정은 [기존 크기만의 ExtraTrees](load_time_findings.md)와 정확히 같다. 게이트를 추가해서 점추정 오차가 낮아졌다고 주장하지 않는다. 규칙은 Qwen의 실패를 확인한 뒤 선택한 **사후 탐색**이며 독립 신규 모델 데이터에서 검증되지 않았다.

## 데이터와 알고리즘

원본은 SQLite 스냅샷 (`../data/audit_v1/measurements.parquet`)이다. 같은 `icns` 호스트, 직접 수집기 v2, CUDA 1개 replica, CPU affinity 2코어, 성공·분석 적격 실행만 선택했다. 주 캠페인은 9모델·575실행이며, 추가 입력 조건 캠페인은 각각 9모델·18/44실행이다. 타깃 `load_seconds`는 로컬에 캐시된 Hugging Face 모델과 토크나이저를 읽는 구간이며 **VM 시작, 이미지 다운로드, GPU 이동, 서비스 준비나 전체 배포 지연이 아니다**. 캐시의 cold/warm 상태 정답도 없다.

모델별 주 캠페인 적재시간 중앙값 한 개와 파라미터 크기로 ExtraTrees 200개를 학습한다. 따라서 요청 조건 64개를 독립적인 64개 신규 모델처럼 학습하지 않는다. 예측 입력은 모델 파라미터 바이트와 작업 종류뿐이다. 파라미터 바이트는 역사적 결과 JSON에서 읽었으므로, 실제 적용에는 응용 등록 시 같은 메타데이터를 제공한다는 가정이 필요하다. 후보의 적재시간·실행 결과는 입력하지 않는다.

게이트는 donor **실행 수가 아닌 서로 다른 모델 수**를 센다. 주 캠페인의 `inference` donor는 8모델, `causal_lm_forward` donor는 1모델이다. 같은 작업 종류 donor가 2모델 이상이면 예측하고, 미만이면 `unsupported_task`를 반환하며 예측초는 빈 값으로 둔다. 같은 작업 종류에서 학습 크기 범위를 벗어나면 별도 경고 플래그를 붙이되, 기본 정책은 이 이유만으로 보류하지 않는다. 게이트 임계값 2와 크기 범위 정책도 이 자료에서의 탐색적 설계다.

## 모델 전체 제외 평가

각 분할은 대상 모델의 모든 실행을 제외하고 주 캠페인의 다른 모델만 학습한다. 대상 모델의 주 조건과 두 다른 입력 조건을 함께 평가한다. 추가 입력 조건 캠페인은 동일 모델의 데이터이며, 수집 시각도 주 캠페인보다 앞서 있으므로 **미래 신규 모델 검증이 아니다**. 모든 기준선에 똑같은 게이트를 적용하고 **답한 모델만** 비교했다.

| 9모델 전체 제외 평가, 답한 8모델의 동일 가중 평균 | 주 조건 | novel 조건 | space 조건 |
| --- | ---: | ---: | ---: |
| 지원 범위 포함 크기 ExtraTrees, MALE | 0.246 | 0.266 | 0.229 |
| donor 모델 중앙값, MALE | 0.320 | 0.322 | 0.302 |
| 크기 로그 Ridge, MALE | 0.338 | 0.372 | 0.314 |
| 전체 정적 특징 Ridge, MALE | 0.324 | 0.352 | 0.306 |
| 지원 범위 포함 크기 ExtraTrees, 절대초 MAE | 0.039초 | 0.040초 | 0.036초 |
| 답변 실행 수 / 전체 실행 수 | 512/575 | 16/18 | 40/44 |

주 조건에서 답한 8모델 중 7모델은 donor 중앙값보다 오차가 작았다. 모델을 단위로 재표집한 **사후 탐색**에서 ExtraTrees와 donor 중앙값의 MALE 차이는 `−0.075`, 95% 분위 범위는 `−0.188~+0.049`였다. 0을 포함하므로 8모델만으로 일관된 우위를 확증할 수 없다. ExtraTrees는 답한 요청에서 기존 모델과 동일하므로 이 표를 이전보다 새로운 예측 성능의 증거로 읽으면 안 된다. Qwen의 기존 크기 ExtraTrees는 모델 전체 제외 주 조건에서 MALE `1.559`, 중앙 적재시간 `1.214초`에 약 `0.255초`를 예측했다. 게이트는 바로 이 **동종 작업 donor 부재**를 표시한다.

## 계열 전체 제외 스트레스 테스트

게이트는 작업 종류의 부재만 찾고, 같은 작업 종류 안의 모델 **아키텍처 차이**를 식별하지 못한다. BERT 4모델 및 BERT+DistilBERT 5모델을 통째로 제외하면 남은 encoder donor가 2개 이상이므로 모두 답한다. 이때 주 조건의 MALE는 다음과 같다.

| 전체 제외 집단 | 답변 모델 | 지원 범위 포함 크기 ExtraTrees | 전체 정적 특징 Ridge | donor 중앙값 |
| --- | ---: | ---: | ---: | ---: |
| BERT 4모델 | 4/4 | 0.278 | **0.231** | 0.410 |
| BERT+DistilBERT 5모델 | 5/5 | 0.417 | **0.394** | 0.574 |

다른 입력 조건에서도 정적 특징 Ridge가 우세했다. BERT 4모델의 novel/space MALE는 Ridge `0.284/0.211`, 이 방법 `0.348/0.270`이었다. 5모델은 Ridge `0.431/0.364`, 이 방법 `0.451/0.392`였다. 따라서 현재 게이트만으로 새 아키텍처 일반화를 해결하지 못한다. 참고할 모델이 없는 작업 종류의 예측을 보류하는 용도로 제한하는 것이 적절하다.

## 산출물과 재현

- [재현 코드](../scripts/evaluate_load_support.py), [모델 분리·보류 동작 테스트](../tests/test_load_support.py)
- 행별 예측과 보류 사유 (`../artifacts/load_support_v1/predictions.csv`), 집계 점수 (`../artifacts/load_support_v1/aggregate_metrics.csv`), 모델별 점수 (`../artifacts/load_support_v1/per_model_metrics.csv`)
- [학습·검증 분할과 donor 목록](../artifacts/load_support_v1/split_manifest.json), 사후 짝지은 재표집 (`../artifacts/load_support_v1/paired_model_bootstrap.csv`)
- 전체 주 캠페인으로 학습한 연구용 모델 (`../artifacts/load_support_v1/support_aware_size_tree_all_main.joblib`), [데이터·코드·모델 해시](../artifacts/load_support_v1/manifest.json)

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_load_support.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_load_support.py' -q
```

실제 배포·Scale-out 판단에는 VM 생성, 서비스 준비, 동적 트래픽과 장애 전이의 정답이 더 필요하다. 이 연구용 모델을 곧바로 운영 판단기로 연결하지 않는다.
