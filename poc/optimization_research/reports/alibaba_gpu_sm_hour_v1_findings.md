# Alibaba GPU 다음 1시간 SM 계측 예측: 탐색 학습 결과 v1

실행일: 2026-10-02 KST. **두 집합 모두 사전 정의한 탐색 개선 게이트 PASS.**
이 7일 자료의 분포와 현재값 유지 오차는 학습 전 진단에서 이미 보았다. 결과는 탐색 성과이며 미사용 기간의 독립 확인 결과가 아니다.

## 1. 문제와 연구 구성

현재값 유지가 강한 시간열에서 최근 수준을 출발점으로 삼고 변화량만 학습하는 방법을 시험했다. 최근 3시간의 수준·변화로 다음 시간의 잔차를 학습하고 현재값에 더하는 경로를 직접 예측 경로와 같은 조건에서 비교했다.

    delta_t = SM_(t+1) - SM_t
    predicted_SM_(t+1) = clip(SM_t + alpha * HGB_delta(past_features), 0, 100)

alpha 후보 0/.25/.5/1 중 검증 점수로 선택했다. 두 집합 모두 alpha=1을 선택했으므로 이번 개선을 수축의 독립 효과로 주장하지 않는다. HGB는 표준 scikit-learn 학습기이며 새 아키텍처 발명이 아니다. 연구 구성은 잔차 정답, 완료된 과거 입력, 작업·날짜 분리, 검증 기반 보정 비율 선택과 적용 범위의 검증이다.

## 2. 데이터와 예제 구성

- 원본: Alibaba Cluster Trace GPU v2026, 상대 날짜 109~115일 모든 시간. 실제 달력 날짜가 아니다.
- 504개 원본 파일·14,501,833,687바이트의 SHA256·크기·행 수 검사는 앞선 진단에서 오류 0건이었다. GPU pod→서버 결합도 100%였다.
- 온라인 추론·GenAI·GPU 요청 양수 1,977,187행에서 시작했다. 정확히 1GPU 요청으로 제한했다.
- 같은 pod·시각 중복 키 5개, 해당 10행을 전부 제외했다. 선택 기간에서 workload가 결측 또는 불일치한 pod 18,259개도 제외했다.
- 유효한 1GPU 요청 관측은 과거 연결 전 743,785행이며, 최근 3시간과 다음 시간의 동일 서버 관측을 갖춘 쌍은 분할 전 718,147개다.
- 이후 workload 해시와 날짜 조건으로 사례를 분리했다. 행·쌍의 수가 독립 응용 또는 독립 날짜 수는 아니다.
- 미래 미관측·이동·불량 계측은 정답 0으로 만들지 않는다. 예측은 다음 시간에도 유효한 동일 서버 관측이 있다는 조건부다. 선택 기간 전체 ID 일관성도 품질 선별에 사용했으므로 운영 시점의 무조건부 평가가 아니다.
- 넓은 집합의 SM은 공개 원시 계측값이다. 다중 GPU 서버의 값을 물리적인 단일 GPU 백분율로 일반화하지 않는다. 엄격한 집합은 과거 3창과 정답 창 모두 서버 GPU 수가 1이다.

| 집합 | 학습 행/workload | 검증 행/workload | 시험 행/workload |
|---|---:|---:|---:|
| 1GPU 요청 pod | 260,613/1,575 | 18,073/285 | 32,172/279 |
| 1GPU 요청 + 단일 GPU 서버 | 48,798/151 | 1,503/27 | 3,461/25 |

- 시간 분할: 학습 day109~112 / 검증 day113 / 시험 day114~115. 각 정답 시간이 해당 분할 안에 있어야 한다. 과거 입력은 자기 workload의 앞선 완료 관측을 사용할 수 있다.
- workload SHA256 앞8자리 정수 modulo100을 0~69/70~84/85~99로 나눴다. train/validation/test 사이 workload와 pod 중복은 모두 0건이다.
- 완료된 시간 t 직후 다음 구간 t+1의 평균을 예측한다. 실제 계측 전달 지연은 제공되지 않아 0을 가정한다. VM·앱 준비/사용자 지연의 예측이 아니다.

## 3. 입력·모델·선택

- 입력: GPU 공개 유형·priority, 최근 SM 3값·평균·표준편차·1/2시간 변화, 관측 hour sin/cos, 서버 GPU 수, CPU 요청 코어.
- ID, 준비 상태, 준비/스케줄 지연, 다음 사용량과 단위 미확정 gpu_mem_request는 입력에 쓰지 않았다.
- 직접 HGB와 잔차 HGB 모두 absolute_error, 150회, learning_rate=.05, max_leaf_nodes=15, min_samples_leaf=50, l2=1, early_stopping=False, seed42다. workload마다 학습 총 가중치를 동일하게 했다.
- 기준선은 현재값 유지, 최근 3시간 평균, 학습 GPU 유형별 정답 중앙값이다. 미지원 유형의 기준선은 학습 전역 중앙값을 쓴다.
- 검증의 (행 MAE + workload 동일가중 MAE)/2로 경로를 선택했다. 두 집합의 최선 단순 기준선은 현재값 유지이며 최종 선택은 잔차 HGB alpha1이다. 시험을 보고 재학습·설정 변경하지 않았다.

## 4. 시험 결과

| 집합 | 지표 | 현재값 유지 | 직접 HGB | 선택 잔차 HGB | 현재값 대비 감소 |
|---|---|---:|---:|---:|---:|
| 1GPU 요청 pod | 행 MAE | 1.781845 | 1.792996 | 1.550651 | 12.97% |
| 1GPU 요청 pod | workload 동일가중 MAE | 1.245778 | 1.304147 | 0.876828 | 29.62% |
| 1GPU 요청 pod | 오차 p95 | 8.068481 | 7.584118 | 6.726690 | 16.63% |
| 1GPU 요청 pod | 10 초과 변화 구간 MAE | 18.843900 | 15.797330 | 15.616301 | 17.13% |
| 1GPU 요청 + 단일 GPU 서버 | 행 MAE | 1.434221 | 1.372109 | 1.353466 | 5.63% |
| 1GPU 요청 + 단일 GPU 서버 | workload 동일가중 MAE | 0.955091 | 0.752413 | 0.712217 | 25.43% |
| 1GPU 요청 + 단일 GPU 서버 | 오차 p95 | 3.855534 | 3.580032 | 3.493727 | 9.38% |
| 1GPU 요청 + 단일 GPU 서버 | 10 초과 변화 구간 MAE | 29.026710 | 27.529971 | 28.344944 | 2.35% |

넓은 집합에서 직접 HGB는 현재값 유지보다 전체 행 MAE가 조금 나빴다. 잔차 경로는 동일 입력·설정의 직접 HGB 대비도 개선했다. 엄격한 집합에서는 잔차 경로가 전체·workload 오차는 낮췄지만 변화 구간 오차는 직접 HGB보다 높았다. 모든 지표에서 잔차가 최선이라고 주장하지 않는다.
엄격한 집합의 alpha=.5는 시험 행 MAE만 보면 alpha1보다 작았지만 검증에서 선택되지 않았다. 최종 결과를 시험 최선 alpha로 바꾸지 않았다.

| 집합 | day114 기준선→모델 MAE | day115 기준선→모델 MAE | workload 차이 95% 구간 |
|---|---:|---:|---|
| 1GPU 요청 pod | 1.803517 → 1.530739 | 1.759329 → 1.571339 | [0.274484, 0.467859] |
| 1GPU 요청 + 단일 GPU 서버 | 1.144268 → 1.056944 | 1.739833 → 1.666002 | [0.025748, 0.548813] |

구간은 workload 단위 2,000회 재표집의 기준선-모델 동일가중 MAE 차다. 양수는 모델 개선이며 고정 모델·이 자료에 조건부다. 날짜는 시험 2일뿐이므로 날짜·클러스터의 일반화 불확실성을 포괄하지 않는다.
탐색 게이트는 검증에서 학습 경로 선택, 시험 행·workload 오차 감소, 변화 구간 오차 비악화, 두 날짜 각각 개선, 재표집 차이 구간 하한 양수의 여섯 조건이며 두 집합 모두 통과했다.

## 5. 재현·민감도와 한계

- 시점·서버 이동·중복·workload/날짜 경계 관련 단위 테스트 8개 통과. 저장 모델을 다시 로드해 두 집합의 8개 예측 경로를 재계산했고 저장 시험 예측과 최대 차이 0이다.
- 넓은 시험은 변화 사례 1,225개·53 workload이며 최대 한 workload가 변화의 39.59%를 차지한다. 엄격한 시험은 변화 108개·5 workload이고 한 workload가 51.85%를 차지한다. 사례 집중도를 함께 제시해야 한다.
- 엄격한 시험의 SM>=80 사례는 0개다. 고사용률 위험 분류의 개선을 검증한 결과가 아니다.
- 넓은 시험에 학습에 없던 GPU 유형 행 1,316개가 있다. one-hot unknown 처리로 계산했으며 새 GPU 하드웨어 일반화 성과로 별도 주장하지 않는다.

| 사후 민감도: 최대 변화 workload 제외 | 남은 행 | 현재값 유지 MAE | 잔차 MAE |
|---|---:|---:|---:|
| 1GPU 요청 pod | 28,882 | 1.426642 | 1.225517 |
| 1GPU 요청 + 단일 GPU 서버 | 3,085 | 0.879062 | 0.752331 |

이 제외 비교는 사후 진단이며 모델 선택·새 독립 확인 시험이 아니다. 알려진 GPU 유형만 남긴 넓은 시험에서도 현재값 유지 1.841014 → 모델 1.603532였지만 이 역시 같은 시험의 하위 집합 분석이다.

- 공개 coarse job/model bucket과 익명 workload를 사용한다. 실제 새 LLM 종류·사용자 서비스 모델 ID로 일반화됐다고 말하지 않는다.
- 메모리 부족/OOM, 실제 VM 수, 배포 성공·실패, 사용자 SLO와 Scale-out/in 조치의 효과는 이 정답에 없다.
- 기존 readiness 우선 제안은 보류한다. 전체 주간 GPU pod에서 준비 상태 충돌 86,598개와 지연 충돌 28,793개가 있었고, 제출 모집단·기한·실패 사유도 없어 배포 실패 라벨로 채택할 수 없다.

## 6. 다음 연구

이 구성과 모델을 고정한 뒤, 이번 기간과 겹치지 않는 미사용 연속 기간의 자료를 확보해 확인한다. 동일 workload 미래 기간 시험과 미사용 workload 시험을 구분하고 현재값 유지·직접 HGB와 같은 기준을 유지한다. 새 기간의 희귀 고사용률 사례 수와 작업별 집중도를 점수 전에 먼저 진단한다. 확인 전 운영 판단기에 채택하지 않는다. 실제 스케일링 효과는 별도의 action/READY/사용자 SLO 연결 데이터로 검증해야 한다.

## 7. 파일과 실행

학습 코드: `/home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour.py`
테스트: `/home/researcher/ai-mcmp/research/tests/test_alibaba_gpu_sm_hour.py`
학습 전 프로토콜: `/home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v1.md`
원본: `/home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days109_115/`
전체 결과: `/home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_hour_v1/`
두 모델: 각 집합 하위 폴더의 `model_bundle.joblib` (직접·잔차 HGB와 선택 정보 포함)
가공 자료/분할: `examples.parquet`, `split_manifest.parquet`
평가: `metrics.json`, 각 집합 `metrics.json`, `test_predictions.parquet`, `validation_selection.json`
검증: `replay_verification.json`; 시각 자료: `comparison.csv`, `comparison.png`, `comparison.pdf`; 실행 로그: `train.log`

새 출력 디렉터리를 사용하는 재실행 예:

```bash
POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python /home/researcher/ai-mcmp/research/scripts/train_alibaba_gpu_sm_hour.py \
  --data-root /home/researcher/ai-mcmp/research/data/external/alibaba_gpu_2026_days109_115 \
  --output /home/researcher/ai-mcmp/research/artifacts/alibaba_gpu_sm_hour_v1_reproduction \
  --protocol /home/researcher/ai-mcmp/research/reports/alibaba_gpu_sm_hour_protocol_v1.md
```

테스트: `POLARS_MAX_THREADS=2 python /home/researcher/ai-mcmp/research/tests/test_alibaba_gpu_sm_hour.py`

최초 학습 실행은 약 43.61초, peak RSS 약 13.38GiB였고 CPU 두 학습 스레드로 수행했다. 신규 산출물은 약 25MB 수준이다. 원본과 기존 실험을 보존했다.

출처: [Alibaba 공식 스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md)
