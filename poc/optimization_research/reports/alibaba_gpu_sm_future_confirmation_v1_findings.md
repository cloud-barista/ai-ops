# Alibaba GPU 다음 시간 계측 예측의 미사용 기간 검증

검증 판정은 **PASS**다. 주 확인 집합에서 고정 모델의 개선이 재현됐다. 기존 7일에서 학습한 모델을 고정하고 바로 다음 미사용 7일에서 평가했다. 이 결과는 같은 공개 클러스터의 미래 기간 확인이며 실제 스케일링 효과 검증은 아니다.

## 고정 설정과 자료

- 새 기간은 상대 날짜 116~122일이며 새 타깃을 읽기 전에 기간과 게이트를 고정했다. 첫 판단의 과거 두 시간만 기존 day115 hour22~23을 사용했다.
- 기존 학습 코드와 두 모델의 SHA256이 동결 설정과 일치했고 평가 전후와 재추론 뒤에도 같았다. 재학습·alpha 변경·새 점수에 따른 모델 선택은 수행하지 않았다.
- 선택 경로는 현재 SM + 잔차 HGB 예측, alpha1이다. 최선 단순 기준선은 원래 검증에서 고정한 현재값 유지다. 직접 HGB와 두 다른 단순 경로는 같은 모델/설정으로 보조 비교했다.
- 온라인 GenAI·1GPU 요청을 대상으로 최근 3개 완료 창과 다음 창의 동일 서버 유효 관측을 연결했다. 중복 pod/시간과 불안정·결측 workload는 제외했다. 미래 미관측을 0으로 만들지 않았다.
- 주 집합은 모든 네 창에서 단일 GPU 서버이며 학습에 사용하지 않은 workload hash bucket85~99다. 넓은 1GPU 요청 집합은 별도 보조 확인이다.
- 원본 파일 168개, 13,595,282,885바이트, 256,279,382행을 CRC 다운로드 검사에 더해 SHA256·행수·컬럼수로 재검산했고 오류는 0건이었다.

## 주 확인과 넓은 집합 결과

| 집합 | 행과 workload | 판정 | 현재값 MAE | 잔차 MAE | 행 오차 감소 |
|---|---:|---|---:|---:|---:|
| 1GPU 요청 pod | 103,306 / 368 | PASS | 2.073320 | 1.814805 | 12.47% |
| 1GPU 요청과 단일 GPU 서버 | 12,097 / 30 | PASS | 1.627406 | 1.481500 | 8.97% |

| 집합 | 지표 | 현재값 | 직접 HGB | 고정 잔차 HGB |
|---|---|---:|---:|---:|
| 1GPU 요청 pod | workload 동일가중 MAE | 2.064950 | 2.149508 | 1.704942 |
| 1GPU 요청 pod | 절대오차 p95 | 9.954691 | 10.337147 | 8.216691 |
| 1GPU 요청 pod | 10SM단위 초과 변화 구간 MAE | 21.546555 | 18.294983 | 18.614230 |
| 1GPU 요청과 단일 GPU 서버 | workload 동일가중 MAE | 0.938041 | 0.775577 | 0.719198 |
| 1GPU 요청과 단일 GPU 서버 | 절대오차 p95 | 4.373794 | 4.534092 | 4.121206 |
| 1GPU 요청과 단일 GPU 서버 | 10SM단위 초과 변화 구간 MAE | 30.129976 | 26.947492 | 27.232106 |

두 집합 모두 잔차 경로가 직접 HGB보다 전체 행·workload 동일가중 오차를 낮췄다. 다만 10SM단위 초과 변화 구간에서는 직접 HGB의 오차가 더 낮았다. 모든 지표에서 잔차 방식이 최선이라는 결론은 아니다.

## 사전 판정 조건과 날짜별 결과

최소 지지는 1,000행·20workload·변화 30개·변화가 있는 workload 5개·7일 전부 관측이다. 개선 게이트는 행/작업별 오차 감소, 변화 구간 오차와 p95의 비악화, 7일 중 최소 5일 행 오차 개선, 작업 단위 재표집 차이 구간 하한 양수다. 어느 하나 실패하면 NO_GO이고 지지가 부족하면 INSUFFICIENT_DATA다.

1GPU 요청 pod: 개선 날짜 7/7일. 기준선-잔차 workload MAE 차의 95% 구간 [0.283788, 0.437440].

지지 검사: {"minimum_rows": true, "minimum_workloads": true, "minimum_change_events": true, "minimum_change_workloads": true, "all_seven_days_present": true}

개선 검사: {"row_mae_lower": true, "macro_workload_mae_lower": true, "change_mae_not_worse": true, "p95_error_not_worse": true, "daily_row_mae_improved_at_least_5_of_7": true, "workload_cluster_ci_lower_positive": true}

| 날짜 | 현재값 MAE | 잔차 MAE |
|---|---:|---:|
| 116 | 2.084271 | 1.843821 |
| 117 | 2.454181 | 2.191664 |
| 118 | 1.901450 | 1.629806 |
| 119 | 2.355471 | 2.073112 |
| 120 | 1.893437 | 1.625088 |
| 121 | 1.959381 | 1.681049 |
| 122 | 1.827386 | 1.619699 |

1GPU 요청과 단일 GPU 서버: 개선 날짜 7/7일. 기준선-잔차 workload MAE 차의 95% 구간 [0.039924, 0.472442].

지지 검사: {"minimum_rows": true, "minimum_workloads": true, "minimum_change_events": true, "minimum_change_workloads": true, "all_seven_days_present": true}

개선 검사: {"row_mae_lower": true, "macro_workload_mae_lower": true, "change_mae_not_worse": true, "p95_error_not_worse": true, "daily_row_mae_improved_at_least_5_of_7": true, "workload_cluster_ci_lower_positive": true}

| 날짜 | 현재값 MAE | 잔차 MAE |
|---|---:|---:|
| 116 | 1.894419 | 1.795020 |
| 117 | 1.861763 | 1.689683 |
| 118 | 1.964574 | 1.833889 |
| 119 | 1.947120 | 1.677284 |
| 120 | 1.435804 | 1.243387 |
| 121 | 1.319868 | 1.205965 |
| 122 | 0.943644 | 0.904966 |

## 보조 집합과 적용 한계

| 집합 | 자료 구분 | 행과 workload | 현재값 MAE | 잔차 MAE |
|---|---|---:|---:|---:|
| 1GPU 요청 pod | 학습했던 workload의 미래 | 430,654 / 1,169 | 2.443815 | 2.002852 |
| 1GPU 요청 pod | 기존 가공 train val test에 없던 workload | 107,629 / 852 | 2.173632 | 1.932946 |
| 1GPU 요청과 단일 GPU 서버 | 학습했던 workload의 미래 | 76,317 / 123 | 2.726348 | 2.056290 |
| 1GPU 요청과 단일 GPU 서버 | 기존 가공 train val test에 없던 workload | 4,431 / 40 | 0.231400 | 0.277001 |

단일 GPU 서버의 완전히 신규인 가공 workload 집합에서는 현재값 유지 MAE 0.231400 → 잔차 0.277001로 **19.71% 악화**됐다. 4,431행·40workload 중 10SM단위 초과 변화는 11개·2workload라 사전 변화 지지도 부족했고, 개선 날짜는 3/7일이었다. 주 확인 PASS를 모든 신규 응용의 일반화 성공으로 표현하지 않는다.

보조 집합은 주 확인 실패를 대체하지 않는다. 신규 workload는 기존 학습·검증·시험의 가공 예제에 없던 익명 ID를 뜻하며 전체 과거 원본에서 한 번도 없었던 새 AI 모델이라는 의미는 아니다.

- 1GPU 요청 pod: 변화 5,143개가 155workload에 분포하며 최대 workload의 변화 점유율은 12.97%다. 다음 SM>=80은 402개다.
- 1GPU 요청과 단일 GPU 서버: 변화 426개가 6workload에 분포하며 최대 workload의 변화 점유율은 55.16%다. 다음 SM>=80은 0개다.

- workload 단위 재표집은 고정 모델·이 일주일에 조건부다. 같은 날짜의 공통 부하나 다른 클러스터 변화를 포괄하는 보장은 아니다.
- 두 층의 개선율을 합산하지 않는다. 넓은 층의 값은 공개 원시 SM 계측이며 다중 GPU 서버에서 물리적인 단일 GPU 백분율로 단정하지 않는다.
- ID 일관성 선별과 다음 시간의 유효 관측이라는 미래 조건이 있다. 실제 계측 전달 지연은 공개 원본에 없어 구간 종료 즉시 이용 가능하다고 가정했다.
- 실제 GPU 부족·OOM·앱 지연 SLO·VM 준비·Scale-out/in 행동 효과는 이 평가의 정답에 없다. 개선 재현 여부와 관계없이 실행 정책의 운영 채택은 별도 데이터와 시험이 필요하다.

## 재현 위치와 검증

원본: `/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days116_122`

동결 설정: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_future_freeze_v1.json`

평가 코드: `/home/researcher/ai-mcmp/research/scripts/evaluate_alibaba_gpu_sm_future.py`

결과: `/home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_future_confirmation_v1/`

모델 해시 전후 일치, 시간 경계, 학습 workload/pod 중복 0건, 저장 예측의 재계산 차이 0, NumPy/Pandas로 별도 계산한 행·작업별·p95 지표 일치를 확인했다. 평가기는 fit을 호출하지 않는다. 원본과 이전 결과는 보존했다.

출처: [Alibaba 공식 스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md)

원래 학습과 상세 방법: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_v1_findings.md`

평가와 학습 금지 테스트 6개가 통과했다. 비교 그래프는 결과 폴더의 `comparison.png`와 `comparison.pdf`에 있다. 평가·검산 실행 로그, 다운로드 로그, 고정 설정과 파일별 해시도 함께 저장했다.

새 결과 경로를 사용한 재실행 예:

```bash
PYTHONDONTWRITEBYTECODE=1 POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python /home/researcher/ai-mcmp/research/scripts/evaluate_alibaba_gpu_sm_future.py \
  --freeze /home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_future_freeze_v1.json \
  --data-root /home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days116_122 \
  --history-root /home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days109_115 \
  --output /tmp/alibaba_gpu_sm_future_confirmation_reproduction
```
