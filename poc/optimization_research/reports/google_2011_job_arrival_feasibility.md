# Google 2011 job 제출 자료의 부하 연구 적격성

2026-09-29. **판정: job 단위 도착과 제출 직후 CPU·RAM 요청량을 관측하는 외부 자료로는 적격하다. 현재 4개 분리 조각의 63개 완전 5분 구간만으로 새 시계열 모델을 학습하지 않는다.** 실제 AI-App `SCALE_OUT/IN` 명령, VM 준비, 사용자 지연 개선의 정답도 아니다.

## 공식 원본과 이번에 새로 확인한 열

Google의 [ClusterData2011 v2.1 설명](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)은 2011년 한 Borg 셀의 29일 자료, 약 12,500대 기계, 압축 약 41GB, CC-BY 조건을 명시한다. 공식 공개 버킷의 [`schema.csv`](https://storage.googleapis.com/clusterdata-2011-2/schema.csv)는 `job_events`의 시각(마이크로초), 결측 표시, job ID, 사건 유형, 익명 user, scheduling class, 익명 job 이름·논리 이름을 정의한다. `task_events`에는 같은 job ID, task index, 사건 유형 및 정규화 CPU·메모리 요청이 있다. `job_events` 자체에는 GPU, 전체 job CPU·RAM 요청, VM 형상, 사용자 요청 지연이 없다. Google의 [v2.1 포맷 설명](https://drive.google.com/file/d/0B5g07T_gRDg9Z0lsSTEtTWtpOW8/view?resourcekey=0-cozD56gA4fUDdrkHnLJSrQ)에서 job의 `SUBMIT=0`, `SCHEDULE=1`, `FAIL=3`, `FINISH=4`, `KILL=5`를 구분한다.

이미 [CPU·RAM 관측 연구](google_2011_observed_resource_findings.md)에 사용한 `task_events`의 `00050/00200/00400/00450`과 같은 번호의 **새** `job_events` 4개 및 `schema.csv`만 받았다. 각 파일의 바이트 수·공식 SHA-256은 [SOURCE](../data/external/google_cluster_2011_2/SOURCE.md)에 있다. 원본 2011 자료는 [공식 저장소](https://storage.googleapis.com/clusterdata-2011-2/)에서 URL `<bucket>/<relative path>`로 재다운로드할 수 있다. [감사 스크립트](../scripts/audit_google_2011_job_arrivals.py)는 로컬 `SHA256SUM` 자체의 고정 해시와 모든 입력 파일의 공식 해시를 확인한 다음 사건을 읽는다. [행·접합·구간별 JSON](../artifacts/google_2011_job_arrival_feasibility/profile.json) SHA-256은 `87dedaf8a072c482d9d97be8707f35993de81daf88f9ada23b354702c42c4893`다. 모델과 예측 점수는 만들지 않았다.

## 접합과 시간 창 진단

| 시간순 조각 | job 사건 행 | job `SUBMIT` ID | 같은 조각 task `SUBMIT`에 연결 | 기존 job의 task 제출도 포함한 task `SUBMIT` 행 | 완전한 5분 구간 | 구간별 job 제출 수 중앙값 / 최대값 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `00050` | 4,770 | 1,573 | 1,573 | 159,854 | 15 | 91 / 123 |
| `00200` | 4,098 | 1,359 | 1,359 | 87,572 | 16 | 79.5 / 114 |
| `00400` | 4,812 | 1,609 | 1,609 | 48,635 | 16 | 100 / 163 |
| `00450` | 6,378 | 2,120 | 2,120 | 116,131 | 16 | 118.5 / 234 |

네 조각 모두 `job SUBMIT`의 첫 task `SUBMIT`은 job 사건 **뒤**에 기록됐다. 첫 task 제출과 job 제출이 같은 마이크로초였던 ID는 0개다. 첫 task 제출까지의 지연 중앙값은 모든 조각에서 **2마이크로초**, 90분위수는 **16/12/16/16마이크로초**, 최대는 **59.611/19.815/0.001051/0.002501초**였다. 1초 안에 첫 task 제출이 보인 job은 **1,555/1,573, 1,349/1,359, 1,609/1,609, 2,120/2,120개**다. 따라서 job ID 접합이 단지 파일 안의 오래된 task를 우연히 붙인 결과는 아니다. 네 조각의 job 사건 결측 표시 행은 0개였다. 각 조각의 `job SUBMIT` ID가 같은 조각에서 `SCHEDULE` ID로도 나타난 수는 1,571/1,358/1,608/2,119개다. 이것은 **같은 ID의 사건 공존**을 센 것이며 배정 지연이나 실행 성공률이 아니다. `task SUBMIT` 중에는 조각 전에 시작한 job의 task도 있으므로, `task SUBMIT` 전체 행을 조각 내 신규 job 요청량으로 세면 과대계산한다.

`job SUBMIT` 뒤 **온전한 60초가 같은 job·task 사건 조각 안에 있는** job은 각각 1,551/1,351/1,603/2,097개다. 그 60초에 처음 제출된 서로 다른 task index의 중앙값은 각 조각 모두 1개지만, 90분위수는 15/15/23/15개, 최대는 10,500/2,000/3,900/5,000개다. 동일 task index의 재제출은 한 번만 센다. 60초 동안 나타난 고유 task index 중 **첫 1초에 이미 나타난 수**는 조각별 **42,164/42,184, 23,329/23,339, 41,302/41,302, 88,889/88,889개**였다. 따라서 `job SUBMIT` 사건만 보고 60초 초기 fanout을 예측하는 연구는 실제 판단에 활용할 시간상의 이점이 거의 없을 수 있다. 첫 1초 task 사건을 직접 읽는 방식이 강한 기준선이다. **다음 5분 신규 job 도착량 예측은 별도 문제**이고, 이를 평가하려면 연속된 사건 파일이 필요하다. CPU·RAM 요청 합계는 **두 요청값이 모두 있는 task `SUBMIT` 행**으로만 계산한다. 이번 네 조각의 task 제출 159,854/87,572/48,635/116,131행에는 두 요청값의 결측이 0개였지만, 이 결과를 다른 조각에 일반화하지 않는다. 합계는 정규화된 **요청**이며 실제 동시 사용량, VM 수나 비용이 아니다. 또한 첫 60초에 뒤늦게 제출되는 task가 있으므로 이를 job 접수 시점에 이미 알려진 특징으로 쓰면 미래 정보 누출이다.

각 조각의 job·task 사건이 공통으로 관측된 시각 범위에서 **양끝이 모두 들어오는** 5분 구간만 셌다. 분리된 4개 조각 사이에는 각각 약 8.6일·11.5일·2.8일의 미수집 간격이 있고, job ID 교집합은 각 쌍 모두 0개다. 63개 5분 구간을 이어 붙여 연속 시계열이나 장기 job 수명을 만들 수 없다. 조각 안 `job SUBMIT`과 종료 사건 ID의 공존도 실행 기간의 완전한 관측을 보장하지 않는다. Google은 [공식 이상 사례](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)에서 일부 job·task 사건 누락과 task 기록이 없는 job을 명시한다.

## 경희대 담당 연구와의 연결 한계

| 질문 | 현재 Google 2011 4개 조각의 답 | 로컬 SQLite와의 관계 |
| --- | --- | --- |
| 어느 시각에 범용 작업 제출이 몰렸는가 | 완전 5분 구간 안의 job 제출 수와 신규 task 요청 CPU·RAM을 **관측**할 수 있다. | SQLite는 모델·형상별 생성 벤치마크이고 운영 부하 시계열이 없어, Google 도착량은 별도의 외부 압박 시나리오다. 두 자료의 익명 ID를 행 단위로 연결할 수 없다. |
| 앞으로 CPU·RAM 요청이 얼마나 올 것인가 | 연속 `job_events`·`task_events`를 더 모으면 **다음 5분 제출 요청량 예측**을 별도 연구할 수 있다. | Google 작업이 AI 응용이라는 표시와 GPU 요구량은 없으므로 SQLite의 AI 모델별 용량을 실제 Google 작업에 배정한 검증으로 해석할 수 없다. |
| 특정 규모로 배포하거나 축소하면 사용자 성능이 좋아지는가 | 실행한 대체 VM 형상과 사용자 지연·처리량의 연결이 없다. | 기존 SQLite의 GPU 1/2 정적 재실행도 온라인 scale 명령 결과가 아니므로 두 자료를 합쳐 이 정답을 만들 수 없다. |

**다음 단계의 수집 조건:** 범용 Borg 부하 예측을 새 과제로 삼을 때는 500개 중 시간상 연속된 `job_events`와 대응 `task_events`를 사전에 날짜·범위로 고정해 확보하고, 모든 원본 해시·결측·경계 job을 감사한다. 후속 탐색 후보를 **`00250`–`00399` 150개 연속 조각**으로 먼저 고정한다. 공식 GCS 객체 목록의 파일 크기 합은 `job_events` **29,896,078 B(28.51 MiB)**, 대응 `task_events` **426,190,963 B(406.45 MiB)**다. 이것은 메타데이터 기반 수집 규모이고 해당 조각의 내용이나 결과는 아직 읽지 않았다. job 도착 수만 예측한다면 작은 job 사건 파일부터 받고, CPU·RAM 요청합을 목표로 할 때만 대응 task 사건을 추가한다. 예측 시점 이전의 완료된 5분 구간만 입력으로 쓰고, 다음 5분의 job 제출 수 또는 새 task의 정규화 CPU·RAM **요청 합계**를 타깃으로 고정한다. 시간 블록 분할과 `직전 값/동일 시각 최근값/최근 이동평균` 기준선을 시험 전에 정하고, job·날짜별 쏠림과 빈 구간을 보고한다. 약 8.7일 분량의 한 셀 자료로 주간 반복이나 다른 AI 서비스 일반화를 입증할 수 없다. 실제 배포·스케일 효과 평가는 별도의 KHU·INNO·ETRI action ID, 명령·준비 시각, 사용자 요청 결과가 연결된 자료가 확보돼야 한다. 정책 시뮬레이터에 Google 도착량을 넣더라도 산출값은 가정한 용량·지연 모형의 **모의 결과**다.

Google의 [2019 v3 추적](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)은 여덟 Borg 셀의 한 달 자료로 약 2.4 TiB 압축이다. GitHub 소개는 BigQuery 전용이라고 적지만, 연결된 [공식 v3 포맷 문서의 JSON access 절](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view)은 공개 GCS `clusterdata_2019_${CELL}` 버킷에서 다섯 테이블의 JSON 압축 조각을 내려받을 수 있다고 설명한다. 이후 cell a `collection_events` JSON 10개를 수집했고 [첫 조각의 실제 행을 감사](google_2019_collection_events_feasibility.md)했다. 이는 2011 job 자료와 별개다. [공식 v3 스키마](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)는 collection·instance 사건과 CPU·메모리 요청·사용량을 정의하지만, [공식 설명](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)은 최종 사용자와 접근 패턴 정보가 없다고 명시한다. v3 역시 AI-App 사용자 SLO나 VM scale 효과의 대체 정답으로 선정하지 않는다.

재현: 연구 디렉터리에서 `python3 scripts/audit_google_2011_job_arrivals.py > artifacts/google_2011_job_arrival_feasibility/profile.json`. 추가 다운로드가 필요하면 [SOURCE](../data/external/google_cluster_2011_2/SOURCE.md)의 각 bucket 상대 경로를 공식 저장소 URL 뒤에 붙인 뒤, 로컬 `SHA256SUM`의 기대 해시를 확인한다.
