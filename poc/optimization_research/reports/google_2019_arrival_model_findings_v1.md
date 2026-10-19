# Google 2019 cell-a 신규 job 도착량 예측: 동결 실험 결과

2026-09-29. **결론: 고정한 Poisson 부스팅 모델은 단순한 직전 1시간 평균보다 크게 나빠 연구 채택 기준을 통과하지 못했다.** 이 자료는 범용 Borg job 제출 압박의 외부 시나리오에는 쓸 수 있으나, AI-MCMP 응용 스케일 조치의 효과나 사용자 SLO를 학습하는 정답은 아니다. **후속 원본 감사:** [공식 특수 시각 `time=0`을 제외하지 않은 영향](google_2019_cell_a_sentinel_impact.md)이 확인됐다. 기존 점수는 그때 동결한 추출 규칙의 결과이며, 정상시각만 쓴 새 모델 성적으로 해석하지 않는다.

## 원본과 사전 동결

[공식 Google 2019 추적](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)의 cell-a `collection_events` 공개 GCS 객체 10개 전부를 사용했다. [공식 prefix 객체 목록](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_a/o?prefix=collection_events-&maxResults=1000&fields=items%28name%2Csize%2Cgeneration%2Cmd5Hash%2Ccrc32c%29%2CnextPageToken)에 있는 10개와 다음 페이지 없음, 객체별 generation·크기·MD5를 메타데이터 manifest (`../data/external/google_cluster_2019_a/collection_events_object_manifest.json`)에 고정했다. 10개 압축 파일 전체 1,334,163,435 B의 MD5·SHA-256을 재검증했고, 검증 manifest (`../data/external/google_cluster_2019_a/verified_collection_events_manifest.json`)의 SHA-256은 `b01dcc58bfbfea500942c7812b0add7242446fcb5c8fa75546d89635447d3e34`다. JSON int64의 10진 숫자 문자열을 엄격히 정수 변환했다.

모델 점수를 보기 전에 [사전 규약](google_2019_arrival_model_protocol_v1.md), [추출·평가 코드](../scripts/evaluate_google_2019_arrival_model.py), [완전한 5분 bin 시계열](../artifacts/google_2019_arrival_model/series.json)의 SHA-256을 [freeze](../artifacts/google_2019_arrival_model/freeze.json)에 기록하고 검증했다. freeze SHA-256은 `5b260980c471e2282856e6ef8d5a675e8f62fd17c044816f45b5e1bc4143fe07`이다. 규약 SHA `260748dceb89fb2b7c9918521ea3040a53a4fcd709506ad957f91637a7f0fe1a`, 코드 SHA `bd040fb236f1d21f386ce12ffb10c01f454515ccb8ce1718cc311d557150283e`, 시계열 SHA `bff66403914c23e7086055ee415fd638bd9fca4025eb977c210b6506dc5b993a`다. 점수를 확인한 뒤 특징·초매개변수·분할·채택 기준을 바꾸지 않았다.

## 라벨 감사와 시간 분할

10개 파일에서 collection 사건 **20,807,441행**, 시간 **0–2,678,998,522,452µs**를 읽었다. `collection_type=0, type=0` 원시 JOB SUBMIT은 **4,727,782행**이었다. 같은 `collection_id`의 최소 유효 SUBMIT 시각만 남기면 **4,727,775개**다. 중복 ID는 **2개**, 추가 제출 행은 **7개**였고 두 ID 모두 객체를 가로질렀다. `missing_type`이 붙은 JOB SUBMIT은 없었다. 익명 ID·user·name은 산출물에 쓰지 않았다.

양끝의 부분 5분 bin을 버리고 **bin 1–8,928**의 8,928개 연속 칸을 구성했다. 이 안에 최초 SUBMIT **4,712,406개**가 있다. 신규 제출 0개인 bin은 **bin 1 하나**였으며, 첫 288-bin burn-in 안이어서 학습·평가 타깃에는 포함되지 않았다. 시계열에 다른 구멍을 0으로 메우는 방식은 쓰지 않았고, 원본 10개를 모두 읽은 뒤 실제 최초 SUBMIT이 없는 bin만 0으로 집계했다. Google 원본 수집 자체의 미기록 사건 가능성은 별개다.

| 시간순 구간 | 완전 bin | 평균 / 중앙값 신규 JOB SUBMIT 수 |
| --- | ---: | ---: |
| train, bin 289–5,472 | 5,184 | 386.3 / 383 |
| validation, bin 5,473–7,200 | 1,728 | 713.3 / 730 |
| test, bin 7,201–8,928 | 1,728 | 770.3 / 753.5 |

## 고정 모델 대 온라인 기준선

모든 방법은 bin 시작 시 이미 닫힌 과거 bin만 썼다. validation MAE로 선정한 대표 기준선은 **직전 12개 bin(1시간)의 평균**이었다. 한 개의 고정 `HistGradientBoostingRegressor(loss="poisson")`는 train에만 맞췄고, validation/test의 직전 관측 제출 수는 다음 bin의 온라인 lag로만 사용했다. train 90분위 **524건/bin**을 고부하 문턱으로 고정했다. [전체 점수 JSON](../artifacts/google_2019_arrival_model/results.json)(SHA-256 `0333b66c23fd414396e3f6f693fbb39b323f7dd9ac568904781754f63bc185b1`)과 validation/test bin별 예측 (`../artifacts/google_2019_arrival_model/predictions.csv`)(SHA-256 `af2a7c181787481652e05739778fae80c44638b9a81e5169b4b168d8e9e6a376`)에 결과를 보존했다.

| 방법 | validation MAE | test MAE | test 90분위 절대오차 | test WAPE | test 고부하 재현율 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 직전 5분 | 98.0 | 135.1 | 310.0 | 17.54% | 95.95% |
| **직전 1시간 평균** | **80.3** | **94.0** | **219.3** | **12.21%** | **97.32%** |
| 직전 24시간 평균 | 159.0 | 148.5 | 308.0 | 19.28% | 91.97% |
| 전날 같은 5분 | 244.1 | 239.7 | 448.0 | 31.12% | 79.88% |
| Poisson 부스팅 모델 | 196.3 | 217.8 | 500.9 | 28.27% | 78.36% |

모델의 test MAE는 대표 기준선의 **2.32배**이고, 90분위 절대오차·고부하 재현율도 모두 나쁘다. 사전 규약의 세 채택 조건이 모두 실패했다. test를 날짜별로 나누어도 모델 MAE가 기준선보다 낮은 날은 없었다. train 평균 386건/bin에 비해 validation/test 실제 평균은 713/770건으로 올라갔다. 모델 예측 평균은 각 551/594건에 머문 반면, 직전 1시간 평균은 711/770건이었다. 따라서 고정 학습기가 새 부하 수준을 따라가지 못한 것이 성능 격차를 설명할 수 있다. 이것은 원인 확정이 아니라 자료와 예측을 토대로 한 해석이다. 직전 1시간 평균은 최근 변화를 바로 반영하는 기준선으로 남는다.

## 적용 범위와 재현

이 결과는 **한 Borg 셀의 약 31일간 신규 job 사건 수**에만 해당한다. 공개 추적에 최종 사용자 요청·접근 패턴이 없고, collection 사건만으로 job별 GPU 필요량·CPU/RAM 실제 동시 사용량·VM 생성/삭제 결과를 알 수 없다. 이 외부 시계열을 로컬 SQLite의 AI 모델 실행 행과 ID로 연결할 수 없다. 따라서 학습 모델이나 1시간 평균을 AI-MCMP `SCALE_OUT/IN` 운영 판단에 바로 넣지 않는다. 과거 bin 집계가 5분 종료 직후 지연 없이 제공된다는 평가 가정도 운영 로그에서 검증되지 않았다. **다음 범용 부하 예측 연구에서는 최근 1시간 평균을 반드시 강한 온라인 기준선으로 두고, 다른 독립 셀 또는 나중 날짜를 먼저 확보한다.** 그 전에는 모델을 조정해 같은 test에 재평가하지 않는다.

재현: 연구 루트에서 `python3 scripts/evaluate_google_2019_arrival_model.py extract`로 10개 원본을 다시 확인하고 시계열 SHA를 대조한다. 동결 파일·코드·원본이 모두 일치할 때 `python3 scripts/evaluate_google_2019_arrival_model.py evaluate`로 점수와 bin별 예측을 재생성한다. 집중 테스트 `python3 -m pytest tests/test_google_2019_arrival_model.py -q`는 숫자 문자열, 행/파일 순서와 중복, 미래값 비사용, 분할, 불완전 원본·동결 변경 차단을 확인한다.
