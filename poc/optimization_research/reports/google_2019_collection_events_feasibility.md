# Google 2019 collection 사건 전체 원본 확보와 첫 조각 감사

2026-09-29. **판정: 2019 원본은 공개 GCS JSON으로 접근 가능하다. 아래 행·필드 감사의 대상인 cell a `collection_events` 첫 조각에는 job·alloc set 사건이 208만 행 있으나 한 달에 걸친 비시간순 일부 행이다. 이후 같은 테이블의 공식 JSON 10개 전체를 수집·바이트 검증했으며, 전체 행을 결합한 시계열 검사는 별도 단계다. 첫 조각만으로 5분별 전체 도착량이나 `SCALE_OUT/IN` 효과를 학습하지 않는다.**

## 출처·형식

[Google 공식 2019 소개](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)는 2019년 5월 여덟 Borg 셀, 약 2.4 TiB 압축, CC-BY 조건과 사용자 접근 패턴 부재를 밝힌다. 소개에는 BigQuery만 제공한다고 쓰여 있으나, 연결된 [공식 v3 포맷 문서의 JSON access 절](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view)은 `clusterdata_2019_${CELL}` GCS 버킷의 JSON 파일도 안내한다. 2026-09-29 공개 GCS API에서 cell a `collection_events-*.json.gz` 객체 10개를 확인했다. 첫 객체와 공식 스키마를 받은 뒤, 남은 아홉 객체도 순차로 받아 **총 1,334,163,435 B**의 압축 파일 전체를 GCS generation·크기·MD5와 로컬 SHA-256으로 검증했다. [객체별 출처·해시와 검증 manifest](../data/external/google_cluster_2019_a/SOURCE.md)를 고정했다. 아래 행·사건 프로파일은 첫 객체 **133,462,892 B**만 스트리밍한 결과다. 압축 해제 복제본은 만들지 않았다.

[공식 스키마](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)와 [GCS의 테이블 스키마](https://storage.googleapis.com/clusterdata_2019_schema/collection_events.schema.json)는 `time`, `type`, `collection_id`, `collection_type`, `scheduling_class`, `priority`, `user`, 익명 collection 이름, `parent_collection_id`, `max_per_machine`, `max_per_switch`, `vertical_scaling`, `scheduler` 등을 정의한다. `collection_type=0`은 job, `1`은 alloc set이다. `EventType`의 이번 관측값은 `0 SUBMIT`, `1 QUEUE`, `2 ENABLE`, `3 SCHEDULE`, `5 FAIL`, `6 FINISH`, `7 KILL`, `8 LOST`, `9 UPDATE_PENDING`이다. 2011 v2와 2019 v3의 사건 번호는 다르므로 혼용하지 않는다. 이 테이블에는 CPU·RAM **요청량 자체가 없고**, v3 `Resources`가 정의한 자원도 정규화 CPU·메모리다. 요청량은 별도 `instance_events`, 실제 사용량은 `instance_usage`, 기계 용량은 `machine_events`를 더 읽어야 한다. `vertical_scaling`은 설정 열이지 실제 증감 명령·사용자 결과가 아니다.

## 전체 조각 스트리밍 결과

| 진단 | 관측값 |
| --- | ---: |
| JSON 행 | 2,081,540 |
| job / alloc set 사건 행 | 2,066,882 / 14,658 |
| `SUBMIT` / `SCHEDULE` / `FINISH` / `KILL` | 475,546 / 519,742 / 133,329 / 380,932 |
| `QUEUE` / `ENABLE` / `FAIL` / `LOST` / `UPDATE_PENDING` | 46,291 / 520,232 / 5,348 / 50 / 70 |
| `time` 최소·최대 | 0 / 2,678,997,112,020 마이크로초 |
| 파일 내 이웃 행의 시각 역전 | 1,041,955회 |
| 행이 하나 이상 나온 5분 칸 | 8,929개 |
| 5분 칸의 **이 조각** 행 수 최소·최대 | 31 / 4,969 |

모든 행에 `time`, `type`, `collection_id`, `collection_type`가 있었다. `missing_type`은 50행에 값 `2`가 있고 나머지는 필드가 없었다. `vertical_scaling` 값 `1/2/3`은 각각 142,859/1,385,768/552,905행, 8행은 필드가 없었다. 이는 **사건 행의 반복 집계**이며 서로 다른 응용 수·실제 scale 조치 수가 아니다. 상세 필드별 건수와 원본 해시는 [프로파일 JSON](../artifacts/google_2019_collection_events_feasibility/profile.json)에 보존했다. JSON SHA-256은 `e6273fc92841d00004cd627e5c6722c21d57615c1c532141c9ad493b383f353f`다.

첫 세 행만 해도 trace 시각이 약 2,181,848초→1,685,778초→65,326초로 이동했고, 전체 파일의 절반 정도 이웃 행에서 시각이 역전됐다. 관측 시각 범위는 한 달 대부분을 가로지른다. **이 파일은 연속된 날짜 조각이 아니다.** 8,929개 5분 칸에 모두 행이 있다고 해서 그 칸의 모든 셀 사건이 들어 있다는 뜻은 아니다. 공개 객체 목록에는 같은 테이블의 JSON 조각이 10개 있고 이들의 정확한 분할·포함 확률은 이번 자료만으로 확인하지 못했다. 따라서 위 5분 행 수는 전체 job 도착률이나 처리량의 라벨로 사용할 수 없다. `SUBMIT` 475,546도 한 조각의 **사건 행 수**이며 신규 job 총수로 부르지 않는다.

## 경희대 연구 결정

- **요구사항 분석·인프라 형상:** AI 모델명·요청 내용·GPU 종류·수량·사용자 지연 SLO가 없다. 이 한 테이블은 CPU·RAM 요청조차 담지 않는다. 기존 SQLite의 모델별 생성 성능·GPU 예약 피크와 ID를 연결할 수 없다.
- **배포·스케일링 판단:** `vertical_scaling` 설정과 `UPDATE_PENDING` 사건을 KHU/INNO/ETRI의 VM scale 명령이나 사용자 성능 결과로 해석하지 않는다. 동일 collection의 다른 조각 사건이 빠졌을 수 있어 완료 생애도 이 한 파일로 보장하지 않는다.
- **부하·자원 위험 신호:** cell a의 collection 사건 압축 원본 10개는 확보했다. 이 모두를 검증·결합해 중복 키와 시간 범위를 확인해야 비로소 **범용 Borg job 제출 수**의 5분 시계열을 평가할 수 있다. CPU·RAM 요청에는 별도 instance 사건, 기계 가용량에는 machine 테이블이 필요하다. 첫 한 조각으로는 시계열 모델을 시작하지 않는다. [Google 2011 작은 job 사건](google_2011_job_arrival_feasibility.md)의 연속 조각은 다운로드 비용이 더 낮은 별도 선택지다.

이후 [10개 객체를 모두 결합한 다음 5분 제출 수 평가](google_2019_arrival_model_findings_v1.md)에서 중복 ID를 제거해 시계열을 만들었다. 고정 학습기는 최근 1시간 평균 기준선보다 성능이 낮아 미채택했다. 이 첫 조각의 부분 표본 통계를 전체 셀 통계로 재사용하지 않았다.

재현: 연구 디렉터리에서 `python3 scripts/audit_google_2019_collection_events.py > artifacts/google_2019_collection_events_feasibility/profile.json`. 감사기는 원본의 GCS 크기·MD5와 고정 SHA-256을 대조한 뒤 gzip을 스트리밍하며 익명 collection ID나 user 원문을 결과 파일에 쓰지 않는다.
