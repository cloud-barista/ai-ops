# Google 2019 ALLOC_SET 시작 지연 학습 적격성: NO-GO

2026-09-29. 이미 내려받아 검증한 Google ClusterData2019의 cell-e·g `collection_events` 전체 JSON gzip을 읽기 전용으로 감사했다. [공식 v3 스키마](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)에서 `collection_type=1`은 ALLOC_SET, `type=0`은 SUBMIT, `type=3`은 SCHEDULE이다. 이는 범용 Borg 자원 묶음의 사건이며 VM Ready나 AI-App `DEPLOY/SCALE_OUT` 명령·결과가 아니다.

## 판정

**`SUBMIT` 후 5초 안에 `SCHEDULE`이 없을 위험은 이 두 셀에서 학습할 수 없다.** 유효한 ALLOC_SET 제출 108,003건 모두 같은 `(cell, collection_id)`의 SCHEDULE 사건이 **제출과 동일한 마이크로초**에 있었다. 지연 양성은 0건이다. 상수 `즉시 SCHEDULE` 기준선이 모든 적격 제출의 이진 정답을 맞히므로 분류기 적합·분할·점수 계산을 진행하지 않았다. 기록된 시간 차이도 전부 0이라 이 자료의 해당 사건쌍에서 연속 지연 회귀 정답의 변동 역시 없다.

| 셀 | 원본 전체 행 | ALLOC_SET 사건 행 | 유효 단일 SUBMIT ID | 같은 시각 SCHEDULE | 5초 초과·미기록 SCHEDULE |
| --- | ---: | ---: | ---: | ---: | ---: |
| e | 5,244,061 | 190,270 | 44,171 | 44,171 | 0 |
| g | 4,096,710 | 264,167 | 63,832 | 63,832 | 0 |

두 파일을 끝까지 압축 해제해 JSON 행마다 `collection_type=1`만 남겼다. `missing_type`이 없거나 0인 사건만 쓰고, 추적 전 `time=0`·추적 후 `time=2^63−1` 및 그 밖의 비정상 시각을 제외했다. 셀과 `collection_id`별로 사건을 모아 SUBMIT이 정확히 하나인 ID만 남긴 뒤, 제출 시각 이상인 첫 SCHEDULE을 찾았다. 유효 사건에서 제출 ID 중복은 두 셀 모두 0건이었다. 제외된 특수 시각 ALLOC_SET 사건은 e 4,347행·g 4,932행, `missing_type`이 0 아닌 행은 e 4행·g 7행이었다. `SCHEDULE`의 `SUBMIT` 이후 최대 시각 차이도 두 셀 모두 **0마이크로초**다.

출처와 무결성은 [cell-e SOURCE](../data/external/google_cluster_2019_e/SOURCE.md)·검증 manifest (`../data/external/google_cluster_2019_e/verified_collection_events_manifest.json`), [cell-g SOURCE](../data/external/google_cluster_2019_g/SOURCE.md)·검증 manifest (`../data/external/google_cluster_2019_g/verified_collection_events_manifest.json`)에 기록돼 있다. 공식 GCS 객체의 로컬 SHA-256은 e `9b847a5e761f74743d2188a009b04242969b4d37544c4f7f96efa10330d597f5`, g `ed7c850444c09d51112f7b15d67324700e01e9768b403e6a294941a241062c13`이다. 이번 감사는 이미 다른 연구에 노출된 e·g 파일의 **라벨 적격성 검사**이며, 독립 시험 결과가 아니다.

이 동시각 기록의 생성 원인이나 실제 자원 준비시간을 원본만으로 확정할 수 없다. 별도 `instance_events`·서비스 요청·배포 명령을 결합하지 않았고, 이 ALLOC_SET 사건을 AI 응용의 인프라 생성 지연 또는 스케일 효과로 재해석하지 않는다. 실제 배포·스케일 판단 학습에는 같은 앱의 명령 발행, VM/replica Ready, 후속 사용자 SLO가 연결된 [사건 데이터 계약](scale_effect_data_contract_v1.md)이 필요하다.
