# Google 2019 cell-b: 온라인 수준 보정의 독립 셀 시험 규약

2026-09-29. **cell-b의 원시 `collection_events` 행을 읽기 전에** 이 방법·기준선·점수·채택 조건을 고정한다. 대상은 다음 5분에 처음 관측되는 **고유 Borg JOB collection SUBMIT 수**다. Google 2019 추적은 최종 사용자 접근 패턴을 포함하지 않으므로 AI-App 요청 SLO, VM Scale-out/in 조치 및 그 효과의 정답으로 사용하지 않는다. cell-a의 시험 결과는 이미 공개됐고 아래 보정계수도 그 결과를 본 뒤 골랐으므로 **cell-a는 개발 자료**, 아직 열지 않은 cell-b만 독립 셀 시험이다.

## 출처·라벨 게이트

- cell-b 공식 GCS `collection_events-` 객체 목록의 **JSON gzip 4개 전체**(`000`–`003`, 508,666,112 압축 B)를 쓴다. 객체 이름·세대·크기·MD5·CRC32C를 `data/external/google_cluster_2019_b/collection_events_object_manifest.json`(SHA-256 `6204a1942f388a5c182da7945ed981673b74b4d6e9e58191ea572372e5e6e773`)에 고정했다. [공식 목록](https://storage.googleapis.com/storage/v1/b/clusterdata_2019_b/o?prefix=collection_events-&maxResults=1000&fields=items%28name%2Csize%2Cgeneration%2Cmd5Hash%2Ccrc32c%29%2CnextPageToken)은 4개 JSON과 별도 Parquet 객체를 구분한다. 공통 [공식 schema](https://storage.googleapis.com/clusterdata_2019_schema/collection_events.schema.json?generation=1596854498220506)의 로컬 SHA-256은 `63e6cea700e31608b982206c375bc091fbb7ae1d6e8ab92ed499050af4c77dc3`이다. 객체가 하나라도 없거나 검증에 실패하면 추출·학습·점수를 하지 않는다.
- 셀·객체를 가로질러 ID를 결합하지 않는다. cell-b 안에서는 `collection_type=0`, `type=0`, `missing_type` 없음인 사건의 동일 `collection_id` 중 **최소 유효 시각**만 신규 JOB 도착으로 센다. 파일/행 순서는 시간순이 아니다. [공식 v3 형식 설명](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view)의 특수 시각 `0`(추적 전)과 `2^63−1`(추적 후)은 사건 수·시간축 양끝에서 제외하고 수를 별도 보고한다. 그 밖의 시간이 정수가 아니거나 음수이면 중단한다. 합성 `missing_type` 사건도 제외·계수한다.
- 네 객체의 유효 일반 사건 최소·최대 시각을 병합해 5분 양끝 부분 bin을 각각 버리고, 그 사이 모든 bin을 만든다. 고유 최초 SUBMIT이 실제로 없는 bin만 0으로 둔다. 빈 bin을 파일 조각의 미수집 구멍으로 추측해 0으로 만들지 않는다. 전체 완전 bin이 **최소 1,164개**가 아니면 점수를 내지 않는다. 세부 감사 수(행 수, 원시/유효/중복/특수 시각 SUBMIT, 완전·0 bin)를 보존하고 익명 JOB ID·user·name은 시계열·점수 산출물에 저장하지 않는다.

## 고정 cell-a 학습기와 온라인 보정

- [cell-a 원 시험](google_2019_arrival_model_findings_v1.md)의 검증 원본 10개와 `series.json`/freeze SHA-256을 확인한다. 기존 [코드](../scripts/evaluate_google_2019_arrival_model.py)의 **train 첫 5,184개 5분 bin만** 사용해 같은 `HistGradientBoostingRegressor(loss='poisson', max_iter=120, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=50, l2_regularization=1, early_stopping=False, random_state=42)`를 다시 맞춘다. cell-a validation/test나 cell-b의 목표값으로 HGB를 재학습하지 않는다. 입력은 기존의 과거 lag·평균·표준편차와 절대 trace bin의 하루 위상이다. scikit-learn `1.5.2`, 스레드 1개를 고정한다.
- cell-b의 최초 **300개 완전 5분 bin(25시간)**은 과거 특징·보정 예측·고부하 문턱을 준비하는 burn-in이다. 목표 점수는 offset 300부터 마지막 완전 bin까지의 **모든** bin이다. bin `b` 시작에서 이미 닫힌 `b-1` 이하의 실제 제출 수만 이용한다. `h_b`는 cell-a 고정 HGB의 cell-b 특징 예측(0 이상), `m_b=mean(y[b-12:b])`는 직전 1시간 관측 평균, `q_b=mean(h[b-12:b])`는 직전 1시간에 당시 예측됐던 HGB 값의 평균이다. **주 방법**은 `max(0, m_b + 0.25*(h_b-q_b))`다. `0.25`는 노출된 cell-a validation/test의 탐색으로 고른 고정 계수이며 cell-b에서 조정하지 않는다. 이 식은 최근 수준을 관측량으로 갱신하면서 cell-a에서 학습한 상대적 시간 변화를 일부 반영한다. 최근 5분 값, 직전 1시간 평균, 직전 24시간 평균, 전날 같은 bin, 보정 없는 HGB를 함께 보고한다. 연구 채택 비교 기준선은 **직전 1시간 평균으로 고정**한다.
- 전제는 닫힌 5분 bin의 사건 수가 다음 bin 시작에 지연 없이 이용 가능하다는 것이다. 이는 공개 trace 오프라인 평가 가정이며 실제 수집 지연·운영 제어 가능성을 증명하지 않는다.

## 고정 점수와 해석

주·대조 방법의 MAE, RMSE, WAPE, 90분위 절대오차, 평균 예측값, 일별 MAE를 모두 기록한다. cell-b burn-in 300bin의 제출 수 90분위를 고부하 문턱으로 고정하고, 시험 구간의 고부하 실제 건수·경보 건수·재현율을 보고한다. 문턱이 0이거나 고부하 양성이 **100 bin 미만이면 채택 게이트를 실패**로 둔다. 연구 채택 조건은 주 방법이 직전 1시간 평균보다 **MAE를 ≥2% 낮추고**, 90분위 절대오차가 기준선보다 높지 않으며, 고부하 재현율 저하가 **1%포인트 이내**인 것이다. 세 조건과 표본 게이트 중 하나라도 실패하면 미채택이다. 통과해도 범용 Borg job 부하의 보조 예측 신호일 뿐 KHU의 AI-App Scale-out/in 정책으로 승격하지 않는다.

점수 전에 원본 메타데이터·공통 schema·cell-a 검증 원본/모델 코드/동결·cell-b 추출 코드·이 규약·평가 코드의 SHA-256과 라이브러리 버전을 `freeze.json`에 쓴다. cell-b 원시 사건을 보거나 다운로드하기 전에 freeze를 완료한다. 원본 다운로드 후에는 각 GCS 세대·크기·MD5·SHA-256을 검증하고, freeze 변경 시 점수를 거절한다. 점수 뒤 계수·특징·분할·문턱을 바꿔 같은 cell-b에서 재선택하지 않는다.
