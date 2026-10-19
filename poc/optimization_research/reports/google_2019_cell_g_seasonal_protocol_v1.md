# Google 2019 cell-g: 계절 잔차 보정의 독립 셀 시험 규약

2026-09-29. 이 규약과 평가 코드를 **cell-g 사건 내용을 다운로드·열기 전에** 동결한다. 대상은 다음 5분에 처음 관측되는 고유 Borg JOB collection SUBMIT 수다. Google 2019 trace에는 최종 사용자 접근 패턴이 없으므로 AI-App 요청 지연, 배포·스케일 명령 또는 명령 효과의 정답으로 사용하지 않는다. cell-a와 cell-b 결과는 이미 노출됐으며 아래 창 길이와 계수 학습에 쓰는 **개발 자료**다. cell-g의 단일 셀 이전 점수만 새 시험이다.

## 원본과 정답

- 공식 GCS `clusterdata_2019_g`의 `collection_events-000000000000.json.gz` **1개 전부**를 사용한다. 공식 객체 generation·크기·MD5·CRC32C는 `data/external/google_cluster_2019_g/collection_events_object_manifest.json`에 고정한다. 공식 공통 `collection_events.schema.json`도 검증한다. 객체가 없거나 해시가 틀리면 점수를 내지 않는다.
- `collection_type=0`, `type=0`, `missing_type` 없음인 JOB SUBMIT만 센다. 동일 `collection_id`에서는 **최소 정상시각** 하나를 선택한다. [공식 v3 형식](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view)의 `time=0`(추적 전)과 `2^63−1`(추적 후)은 시간축·정답에서 제외하고 별도로 계수한다. 그 밖의 필수 정수·스키마가 잘못되면 중단한다.
- 정상 사건 최소·최대 시각에서 양끝 부분 5분 bin을 버리고 내부의 모든 완전 bin을 만든다. 첫 300개 bin은 과거 특징과 고부하 문턱을 준비하는 burn-in이다. 그 뒤 모든 bin을 점수화한다. 완전 bin이 1,164개 미만이면 실패다. 원시 ID는 임시 SQLite에서만 중복 제거하고 산출물에는 저장하지 않는다.

## 방법과 학습

이전 두 셀의 [정상시각 cell-a 재추출](google_2019_cell_a_sentinel_impact.md)과 [cell-b 시계열](google_2019_cell_b_adaptive_findings_v1.md)을 쓴다. cell-a 원 v1은 `time=0` 오분류가 있어 학습에 사용하지 않는다. 각 셀의 처음 300개 bin 이후를 개발 예제로 사용한다. 셀의 규모가 다른 문제를 막기 위해 각 셀의 MAE를 그 셀의 **전날 같은 5분 값 MAE**로 나누고 두 비율을 같은 가중치로 평균한다.

창 길이 **2개 bin**은 이미 노출된 cell-a·b 탐색에서 정한 고정 구조다. 목표 bin `t`가 시작될 때 닫힌 과거 bin만 읽는다. `d_t=y[t−288]`는 전날 같은 시각 값, `r_t=((y[t−1]−y[t−289])+(y[t−2]−y[t−290]))/2`는 최근 두 bin에서 측정한 전날 대비 변화다. 주 예측은 `max(0, d_t+αr_t)`다. `α∈{0, 0.05, …, 1.00}`에서 위의 **셀 균등 정규화 MAE**를 최소화하는 값을 택하며, 동률이면 작은 `α`를 택한다. 이 한 계수와 개발 점수는 cell-g를 열기 전에 freeze에 기록한다. cell-g 정답으로 계수를 갱신하거나 재학습하지 않는다.

cell-g의 주 비교 기준선은 **전날 같은 5분 값**이다. 직전 1시간 평균, 직전 5분 값도 함께 보고한다. 고부하 문턱은 cell-g burn-in 300개 정답의 90분위(`higher`)다. 예측치가 문턱 이상이면 고부하 경보다. 입력의 각 5분 bin이 종료 즉시 관측 가능하다는 오프라인 가정을 둔다.

## 사전 채택 게이트와 재현

각 방법의 MAE, RMSE, WAPE, 90분위 절대오차, 고부하 경보율·정밀도·재현율을 기록한다. 주 방법은 전날 같은 5분 값보다 **MAE ≥5% 개선**, **90분위 절대오차 악화 없음**, **고부하 재현율 저하 ≤1%포인트**, **고부하 경보율 증가 ≤5%포인트**를 모두 충족해야 한다. 문턱이 0이거나 실제 고부하 bin이 100개 미만이면 게이트 실패다. 통과해도 범용 Borg 도착 부하의 보조 예측 연구 결과일 뿐 AI-App 배포·스케일 정책에는 채택하지 않는다.

`artifacts/google_2019_cell_g_seasonal_v1/freeze.json`에 개발 시계열·원본 검증 manifest, cell-g 객체 메타데이터·schema, 추출·평가 코드, 이 규약의 SHA-256과 라이브러리 버전, 학습된 `α`를 **cell-g 원본 fetch 전** 기록한다. fetch와 평가는 이 freeze 및 공식 객체 크기·MD5·로컬 SHA-256이 일치할 때만 수행한다. cell-g를 본 뒤 파라미터나 게이트를 조정한 결과는 이 시험의 독립 점수가 아니다.
