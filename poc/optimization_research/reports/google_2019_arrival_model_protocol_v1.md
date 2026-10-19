# Google 2019 cell-a 신규 job 도착량 예측: 사전 규약 v1

2026-09-29. **대상은 Borg cell-a의 다음 5분 `JOB collection SUBMIT` 최초 발생 수**다. AI-MCMP 응용 요청량, VM 스케일 명령, 사용자 SLO 또는 경희대 정책 효과의 정답이 아니다. 한 원본 조각만으로 만든 5분 집계는 불완전하므로 **공식 `collection_events` 10개 객체 전부의 무결성이 검증되지 않으면 데이터 집계·학습·점수 계산을 하지 않는다.**

## 원본 게이트와 타깃

- 공식 GCS `clusterdata_2019_a`의 `collection_events-000000000000.json.gz`부터 `...000000000009.json.gz`까지 정확히 10개를 쓴다. [공식 GCS prefix 목록](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_a/o?prefix=collection_events-&maxResults=1000&fields=items%28name%2Csize%2Cgeneration%2Cmd5Hash%2Ccrc32c%29%2CnextPageToken)의 응답에서 이 이름 10개와 다음 페이지 없음이 확인됐다. 객체 목록의 이름·크기·generation·MD5를 고정한 `data/external/google_cluster_2019_a/collection_events_object_manifest.json`의 SHA-256은 `452c60360ff98db27d10417494361a664c7ef34da229ef3093e9df9149f12f89`다. 첫 객체의 압축 SHA-256은 `2288aa6bb4ef5157a68ca73719685b5bf438edcc85ef5ea5eb472c294cb271d4`, 스키마 SHA-256은 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`다. 나머지 9개까지 모든 압축 SHA-256을 기록한 `verified_collection_events_manifest.json`의 SHA-256은 `b01dcc58bfbfea500942c7812b0add7242446fcb5c8fa75546d89635447d3e34`다. 이 manifest 자체의 SHA-256을 점수 계산 전 동결 파일에 기록한다. 이름·객체 메타데이터·전체 10개 로컬 SHA-256을 매 실행 확인한다.
- JSON 각 행의 `time`, `type`, `collection_type`, `collection_id`가 없거나 스키마 정수를 나타내는 10진 숫자 문자열/정수가 아니면 중단한다. BigQuery JSON 직렬화의 int64 문자열을 정수로 변환하며, 빈 문자열·부호·음수 시각/ID는 허용하지 않는다. `0` 시각은 허용한다. `collection_type=0`(job), `type=0`(SUBMIT)만 후보로 쓴다. 이 중 `missing_type`에 값이 있는 행은 정답에서 제외하고 수를 보고한다. 다른 collection 사건, alloc set, `SCHEDULE`, `FINISH`, `KILL`, `UPDATE_PENDING`는 신규 job 제출이 아니다.
- **같은 `collection_id`의 유효 SUBMIT이 여러 번 나오면 모든 객체를 통틀어 최소 `time` 하나만** 신규 job 도착으로 센다. 객체 순서나 JSON 행 순서로 최초를 정하지 않는다. 원시 SUBMIT 행 수, 제외 행 수, 유효 행 수, 고유 ID 수, 중복 ID 수와 객체 간 중복 ID 수를 별도 보고한다. 익명 ID·user·name은 최종 산출물에 저장하지 않는다.
- 모든 객체를 병합한 `time`의 전역 최소·최대에서 경계 5분 칸을 각각 버린다. `W=300,000,000µs`, `first_full_bin=floor(min_time/W)+1`, `last_full_bin=floor(max_time/W)-1`(양끝 포함). 이 범위의 모든 정수 bin을 만들고, 유효 최초 SUBMIT이 없는 bin은 **0**으로 둔다. 입력 파일 순서와 행 순서를 시간축으로 쓰지 않는다. 이 규칙의 “완전”은 전체 collection-events 객체와 trace 양끝 제거를 뜻하며, Google 수집 자체의 누락 가능성이 0이라는 뜻은 아니다.

## 예측 시점과 분할

- bin `b` 시작 시점에 다음 닫히지 않은 5분 구간의 최초 SUBMIT 수 `y_b`를 예측한다. 입력은 `b-1` 이하의 완료된 bin만 사용한다. 목표 bin의 사건, 뒤늦은 완료/실패, 다른 테이블의 미래 정보는 사용하지 않는다.
- 첫 288개 완전 bin은 과거 24시간 특징 확보를 위한 burn-in이며 점수에서 제외한다. 남은 bin을 시간순으로 **첫 60% train, 다음 20% validation, 마지막 20% test**로 고정한다. `n_train=floor(0.6n)`, `n_validation=floor(0.2n)`, 나머지를 test로 둔다. 셋 중 하나가 288 bin 미만이면 중단한다. 날짜나 사건량에 따라 경계를 움직이지 않는다.
- 검증·시험 기간의 예측은 **한 bin씩 갱신되는 온라인 관측**을 가정한다. 이미 끝난 이전 validation/test bin의 실제 제출 수는 다음 예측의 lag로 이용할 수 있지만, 해당 기간 정답으로 모델을 재학습하거나 하이퍼파라미터를 바꾸지 않는다. 실제 지연 수집이 있다면 이 가정은 별도 재검증이 필요하다.

## 비교 대상과 고정 학습기

- 온라인 기준선 4개: 직전 5분 `y[b-1]`, 직전 1시간 평균 `mean(y[b-12:b])`, 직전 24시간 평균 `mean(y[b-288:b])`, 전날 같은 5분 `y[b-288]`. 모두 음수가 아닌 실수 예측이다. validation **MAE가 최소인 기준선**을 대표 기준선으로 선택하고, 동률이면 방금 적은 순서를 쓴다. test를 보고 기준선을 고르지 않는다.
- 학습 특징: `lag_{1,2,3,6,12,24,288}`, 끝난 3·12·24·288 bin의 평균, 끝난 12·288 bin의 모집단 표준편차, `b mod 288`의 sin/cos. 다른 특징은 없다. 하루 위상은 trace 상대시각이다. train feature/target만으로 `HistGradientBoostingRegressor(loss="poisson", max_iter=120, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=50, l2_regularization=1.0, early_stopping=False, random_state=42)` 한 개를 학습한다. sklearn `1.5.2`에서 실행하고 1개 스레드로 제한한다. 출력은 `max(0, prediction)`으로 자른다. 초매개변수 탐색과 test 재학습은 없다.

## 평가와 채택 규칙

- validation/test의 **MAE**, RMSE, WAPE(`sum(abs error)/sum(actual)`), 90분위 절대오차를 전 방법에 보고한다. train 정답의 90분위수를 고부하 문턱으로 고정하고, validation/test에서 `actual>=threshold` bin의 예측 `>=threshold` 비율(재현율)과 각 방법의 경보 건수도 보고한다. zero actual 합이면 WAPE는 정의되지 않음으로 둔다. 전체 test 일별 MAE와 일별 제출 수를 함께 보여, 평균 개선이 특정 날에만 나타나는지 확인한다.
- 학습기 연구 채택 기준: **test MAE가 validation에서 선정한 기준선보다 5% 이상 낮고**, test 90분위 절대오차가 그 기준선보다 높지 않으며, 고부하 재현율도 낮지 않아야 한다. 어느 하나라도 실패하면 이 고정 후보는 탈락이다. 이 기준으로도 한 셀·한 달의 범용 job 압박 신호 가능성만 말할 수 있고 AI-App 운영 정책에는 투입하지 않는다. test 결과를 보고 다른 모델이나 분할을 고르는 경우 별도 탐색으로 표시한다.
- per-bin 예측 CSV, 전체 평가 JSON, 원본·스크립트·규약 SHA-256, 분할 경계, 실행 패키지 버전, 원시/고유 SUBMIT 감사 수를 저장한다. 검증된 manifest와 스크립트·규약의 SHA를 기록한 freeze 파일이 없으면 평가 명령은 실패해야 한다. 이 파일은 **점수 계산 전에** 작성하며 SHA가 달라지면 새 규약으로 다시 시작한다.
