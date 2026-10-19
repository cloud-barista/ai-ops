# Google 2019 cell-b 독립 셀 시험: 온라인 수준 보정 결과

2026-09-29. **고정한 온라인 보정 학습기는 연구 채택 게이트를 통과하지 못했다.** cell-a에서 학습한 Poisson HGB의 수준 오차를 cell-b의 최근 1시간 관측으로 보정하면 MAE는 최근 1시간 평균의 `48.63`에서 `47.84 JOB/5분`으로 **1.61%** 낮아졌다. 그러나 [사전 규약](google_2019_cell_b_adaptive_protocol_v1.md)의 최소 **2% 개선**에 못 미쳤다. 같은 시험의 단순한 **전날 같은 5분 값**은 MAE `39.90`으로 더 정확했다. 따라서 이 모델을 KHU의 AI-App 스케일 판단에 적용하지 않는다.

**학습 원본 해석:** 이 시험은 [cell-a의 특수 시각 미제외 동결 모델](google_2019_cell_a_sentinel_impact.md)을 처음 보는 cell-b에 적용했다. cell-b 원본은 특수 시각을 제외했지만, cell-a 학습 시계열에는 추적 전 `time=0` 사건 때문에 두 학습 bin의 1건씩 누락과 분할 경계 변화가 있다. 아래 점수는 그대로 재현되며, 정상시각만으로 cell-a를 다시 학습한 모델의 점수로 바꿔 읽지 않는다.

## 원본·동결·라벨

[Google 공식 2019 trace 설명](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)의 cell-b에서 공식 `collection_events` JSON gzip **4개 전부**를 다운로드했다. 객체별 generation·크기·GCS MD5와 로컬 SHA-256을 검증 manifest (`../data/external/google_cluster_2019_b/verified_collection_events_manifest.json`)(SHA-256 `09571fc6262abca9a54cae00d951be7c9f08e49b890280a00b997904261ddf62`)에 기록했다. 압축 총량은 **508,666,112 B**다. 원본 사건을 열기 **전**에 cell-a 고정 원본·모델, cell-b 메타데이터·schema, [추출 코드](../scripts/prepare_google_2019_cell_b.py), [예측 코드](../scripts/evaluate_google_2019_cell_b_adaptive.py), 규약·라이브러리 버전을 [freeze](../artifacts/google_2019_cell_b_adaptive_v1/freeze.json)(SHA-256 `5b98c60c2bc2b414404c5cd71de4be8897895017c550c48e4421320f4ce19618`)에 고정했다. cell-a의 고정 validation/test HGB 예측 **3,456건**이 재학습 후 재현되는 것도 확인했다.

네 객체의 collection 사건 **7,975,436행**을 모두 읽었다. [공식 v3 형식의 특수 시각](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view) `0`인 추적 전 사건 **45,996행**은 시간축과 정답에서 제외했고, 그중 JOB SUBMIT은 **11,907행**이었다. 추적 후 시각 `2^63−1`은 0행이었다. 유효한 시간의 JOB SUBMIT은 **1,016,119행**, 고유 최초 `collection_id`도 **1,016,119개**로 중복은 없었다. 빈 5분 bin은 0개였고, 양끝 부분 bin을 버린 **8,926개 완전 bin**을 얻었다. [시계열](../artifacts/google_2019_cell_b_adaptive_v1/series.json)(SHA-256 `8dcdf90d5487e1eafd1ea4f5f0b6d1ca2002d83da069ce6553e98dbd07a9c439`)과 예측 결과에는 원시 ID를 남기지 않았다.

첫 300개 완전 bin을 과거 특징·고부하 문턱 준비에만 쓰고, 이후 **8,626개 bin**을 모두 시험했다. cell-b 정답으로 모델을 다시 학습하거나 계수 `0.25`를 바꾸지 않았다. 이 계수는 이미 노출된 cell-a validation/test를 보고 고른 개발 값이다. 예측 시점에는 완료된 과거 bin만 사용한다는 오프라인 가정을 둔다.

## 사전 기준선과 결과

| 예측 방법 | MAE ↓ (JOB/5분) | 90분위 절대오차 ↓ | 고부하 재현율 ↑ |
| --- | ---: | ---: | ---: |
| 최근 5분 유지 | 55.68 | 188.0 | 10.41% |
| **최근 1시간 평균(사전 비교 기준선)** | **48.63** | **130.92** | **0.32%** |
| 최근 24시간 평균 | 51.40 | 124.64 | 0% |
| **전날 같은 5분** | **39.90** | **92.0** | **34.07%** |
| cell-a 고정 HGB | 92.64 | 135.45 | 4.73% |
| **최근 수준으로 보정한 HGB** | **47.84** | **124.74** | **0.32%** |

고부하 문턱은 cell-b 첫 300 bin 제출 수의 90분위 **289 JOB/5분**으로 점수 전에 정한 규칙으로 계산했고, 시험에 양성 **317 bin**이 있었다. 보정 모델과 최근 1시간 평균의 재현율은 모두 `1/317`에 불과하다. 전날 같은 시각 기준선은 `108/317`을 찾았다. 보정 모델은 최근 1시간 평균보다 31개의 날짜 구간 모두 MAE가 낮았지만, 마지막 구간은 1 bin뿐이고 전체 개선도 2% 게이트에 못 미쳤다. 전날 같은 시각 값은 31개 구간 중 26개에서 보정 모델보다 낮은 MAE를 보였다. cell-a에서 학습한 고정 HGB의 평균 예측은 `187.41`로 실제 평균 `113.01`보다 높았고, 보정 후 평균은 `113.06`이었다. 이 분포 이동과 보정의 역할은 예측값을 토대로 한 해석이며 운영 인과 효과가 아니다.

[전체 지표·일별 성적](../artifacts/google_2019_cell_b_adaptive_v1/results.json)(SHA-256 `96fa80724eed4e3b8c566b15e07637da670b9eea73e2fcf7dfd634aedb5521ef`)과 bin별 예측 (`../artifacts/google_2019_cell_b_adaptive_v1/predictions.csv`)(SHA-256 `cf5daf492f69d7c570bfbf64571c74a39108cf2c30d5819c04dac428b6e3cf14`)을 보존했다. 원본 4개를 재검증한 뒤 `python3 scripts/evaluate_google_2019_cell_b_adaptive.py evaluate`로 재현할 수 있다. 추출·누출·동결을 포함한 집중 테스트 **26개**가 통과했다.

이 시험은 **범용 Borg JOB 제출 압박**의 독립 셀 비교다. Google trace에는 최종 사용자 접근 패턴이 없고, 여기에는 배포 명령·준비된 VM 수·후속 AI 요청 SLO가 연결되지 않는다. 따라서 간단한 전날 동시간 기준선이 이긴 결과도 AI-App `SCALE_OUT/IN` 정책의 정확도나 비용 효과를 입증하지 않는다. 같은 cell-b 점수를 보고 α, 특징 또는 문턱을 바꿔 다시 독립 시험으로 보고하지 않는다.
