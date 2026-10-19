# Google 2019 JOB 실행 시작 지연: 누수 없는 학습 규약

작성일: 2026-09-29. **현재 상태는 탐색 감사 이후의 사전 실험 규약 초안이다.** Cell e·g의 `SUBMIT→SCHEDULE` 빈도와 일부 제출 시점 속성별 비율을 이미 열람했으므로 이 두 셀의 후속 점수는 독립 검증으로 부르지 않는다. Cell f·h의 사건 내용은 열지 않았다. 아래 방법·코드·기준선·게이트를 해시로 동결하고 개발 게이트를 통과하기 전에는 f·h를 수집하거나 열지 않는다.

## 연구 질문과 자료 범위

질문은 **범용 Borg JOB이 제출 후 5초 안에 실행을 시작하지 못할 확률**이다. 이는 신규 JOB 도착량, 배정 후 CPU·RAM 사용량, 조기 추방과 다른 지도학습 정답이다. Google의 [공식 2019 설명](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)은 8개 Borg 셀의 한 달 자료이며 최종 사용자 접근 패턴은 없다고 명시한다. [공식 v3 proto](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)는 `collection_type=0`을 JOB, `SUBMIT=0`을 스케줄러 제출, `SCHEDULE=3`을 collection 실행 시작으로 정의한다. `QUEUE=1`은 배치 스케줄러에서 배정 부적격, `ENABLE=2`는 적격 상태다. 따라서 이 정답을 VM 생성·실제 배치 위치·모든 replica의 Ready·사용자 요청 SLO라고 부르지 않는다.

개발 원본은 이미 노출된 다음 **완전한 `collection_events` 테이블**이다. 파일의 객체 generation·크기·MD5·로컬 SHA-256은 각 `SOURCE.md`와 검증 manifest에 있다. 파일 내부 행은 시각순이 아니므로 한 파일의 앞부분이나 첫 JSON 객체만으로 접합하거나 0건 구간을 판단하지 않는다.

| 용도 | 로컬 원본 | 검증 메타데이터 |
| --- | --- | --- |
| 개발 cell e | `collection_events-000000000000.json.gz` (`../data/external/google_cluster_2019_e/collection_events-000000000000.json.gz`) | [`SOURCE.md`](../data/external/google_cluster_2019_e/SOURCE.md), `verified_collection_events_manifest.json` (`../data/external/google_cluster_2019_e/verified_collection_events_manifest.json`) |
| 개발 cell g | `collection_events-000000000000.json.gz` (`../data/external/google_cluster_2019_g/collection_events-000000000000.json.gz`) | [`SOURCE.md`](../data/external/google_cluster_2019_g/SOURCE.md), `verified_collection_events_manifest.json` (`../data/external/google_cluster_2019_g/verified_collection_events_manifest.json`) |

각 셀의 collection 사건 스키마 (`../data/external/google_cluster_2019_g/collection_events.schema.json`)에는 제출 당시 `scheduling_class`, `priority`, `scheduler`, `parent_collection_id`, `start_after_collection_ids`, 배치 제한 설정 등이 있다. `machine_id`와 `resource_request`는 이 테이블에 없다. 후자를 쓰려면 별도 `instance_events` 접합과 새 규약이 필요하다. Cell a·b는 과거 도착량 연구에서 사용했으므로 이번 동결 실험의 독립 시험으로 쓰지 않는다. Cell f·h는 개발 데이터나 하이퍼파라미터 선택에 쓰지 않는다.

## 관측 단위와 5초 정답

1. 공식 객체 목록의 전체 JSON gzip을 검증하고 스트리밍한다. `time`, `type`, `collection_type`, `collection_id`는 정수 또는 십진 정수 문자열만 허용한다. [공식 v3 형식 문서](https://drive.google.com/file/d/10r6cnJ5cJ89fPWCgj7j4LtLBqYN9RiI9/view)의 추적 전 `time=0`과 추적 후 `2^63−1`은 일반 시각 사건에서 제외한다. `missing_type`이 0 이외인 합성 사건은 접합에서 제외하고 사건 종류별 건수를 기록한다.
2. `(cell, collection_id)`별 **첫 유효 JOB `SUBMIT` 한 건**을 기준으로 삼는다. 유효 제출이 둘 이상이거나 제출 전에 정상시각 `SCHEDULE`·종료 사건이 있는 ID는 모호 사례로 빼고 수를 기록한다. `SCHEDULE`은 같은 키의 제출 이후 첫 유효 사건을 사용한다. 같은 시각에 상충하는 첫 사건이 있으면 별도 모호 사례로 뺀다. JOB 키는 접합·중복 검사에만 쓰고 모델 입력과 공개 예측 산출물에 쓰지 않는다.
3. 셀의 정상시각 사건 최대값을 `T_end`로 둔다. `t_submit + 5초 ≤ T_end`인 제출만 관측 가능하다. 경계 근처 제출은 **음성으로 채우지 않고 검열 제외**한다. 시간 분할 경계에서도 학습 표본은 `t_submit+5초 ≤ 학습 구간 끝`이어야 한다. `T_end`, 남은 추적 길이, 파일 번호는 모델 특징이 아니다.
4. `Y_late=0`은 첫 유효 `SCHEDULE`이 `t_submit` 이상 `t_submit+5초` 이하에 기록되고, 그보다 앞선 `KILL/FAIL/FINISH/LOST`가 없는 경우다. 그 안에 적격 시작 기록이 없으면 `Y_late=1`이다. 종료 사건이 먼저 나오면 첫 5초 안의 미시작 경쟁 사건으로 따로 센다. 시작과 종료가 정확히 같은 시각이면 모호 사례로 제외한다. `UPDATE_PENDING`, `QUEUE`, `ENABLE`은 제출 이후 사건이므로 특징에 넣지 않고, 정답 뒤 원인 분석에서만 보고한다. 업데이트 후 시작했다면 이는 원래 요청 형상의 인과효과가 아니라 **기록된 요청 경로의 시작 여부**다. 이 동시각·검열 규칙을 합성 테스트로 동결한다.

앞선 읽기 전용 가능성 감사는 **5분의 보수적 완전 관측 창**과 특수시각·결측 제외를 썼다. 그 기준에서 e는 853,160개 제출 중 120,215개, g는 777,670개 중 105,024개가 5초 초과였다. 이번 규약의 정확한 5초 창, 모호 사건 제외를 적용하면 분모가 달라질 수 있다. 이 수치는 학습 점수나 사전 등록된 효과 추정치가 아니다. 2011의 [배정 지연 감사](google_2011_placement_delay_feasibility.md)는 다른 trace·event schema에서 미배정 사례가 소수 job에 몰려 중단된 별도 결과다.

## 제출 시점 입력과 누수 차단

이번 구현의 정적 특징은 **그 JOB의 원본 `SUBMIT` 행**에서만 읽은 여섯 개다: `scheduling_class`, `priority`, `scheduler`, `has_alloc_collection`(`alloc_collection_id`가 유효한 비영 ID), `has_parent_collection`(`parent_collection_id`가 유효한 비영 ID), `start_after_count`(`start_after_collection_ids` 개수). `alloc_collection_id=0`은 공식 proto의 `NO_ALLOC_COLLECTION`이므로 단순 필드 존재를 alloc 사용으로 세지 않는다. `parent_collection_id=0`도 무부모로 취급한다. 결측값은 결측으로 인코딩한다. ID 자체, 익명 `user`·`collection_name`·`collection_logical_name`, 다른 시점 행에서 채운 값, 실제 `SCHEDULE` 시간, 배정 기계, 이후 사용량은 입력에서 금지한다. 현재 파생 CSV에는 익명 user 키를 남기지 않으므로 사용자 단위 평가를 할 수 없다. 사용자 집중도는 원본을 재감사하는 별도 후속 연구가 필요하다. `max_per_machine`, `max_per_switch`, `vertical_scaling`, 제출 시각의 하루 중 시각·요일은 이번 모델에 넣지 않으며 별도 후속 가설로 다룬다.

온라인 특징은 각 예측 시각 `t`에 **이미 정답 관측이 끝난** 같은 셀의 과거 제출만 사용한다. 최근 1시간 집합은 `t-3600초 ≤ t_i`이면서 `t_i+5초 < t`인 JOB으로 정의한다. 동시각 사건의 처리 순서가 불명확하므로 같은 시각에 막 닫힌 창은 제외한다. 그 집합의 전체 지연율과 `scheduler×scheduling_class×priority`별 지연율을 계산한다. 최초 1시간은 burn-in으로 채점하지 않는다. 과거 타깃 창이 아직 닫히지 않은 JOB, 현재 JOB, 이후 발생할 `QUEUE/ENABLE/SCHEDULE`은 온라인 집계에 포함하지 않는다. 전체 원본을 시간순으로 정렬한 후 **예측 직전까지의 prefix**만 이용해 집계해야 한다. 추출 결과의 과거 특징을 사건 뒤 정답으로 덮어쓰는 전처리는 금지한다. 이 온라인 계산은 사건 시각 직후 로그가 지연 없이 보인다는 오프라인 가정이며, 실제 연동에서는 수집 지연을 측정하고 다시 평가해야 한다.

## 개발, 강한 기준선, 독립 시험

- e·g 각각 trace 시작을 기준으로 day `0–19`를 학습, `20–24`를 검증, `25–30`을 노출된 개발 스트레스 점검으로 고정한다. 구간 끝을 넘는 5초 정답은 앞 구간의 학습·선택에 쓰지 않는다. e·g의 예측 행은 서로 섞어 분할하지 않고 `(cell, collection_id)`를 단위로 유지한다. 스트레스 점수는 탐색 결과로만 명시한다.
- 기준선은 e·g 학습 구간의 셀별 상수 지연율, 앞서 정의한 **최근 1시간 전체 지연율**, `scheduler×class×priority` 최근 1시간 지연율, 제출 당시 여섯 특징이 전부 같은 JOB의 최근 1시간 지연율이다. 최근 그룹 지연율은 셀 전체 최근율로 수축하고 강도 후보를 `10·50·200`으로 고정한다. 또한 학습 구간에서만 계산한 정적 `scheduler×class×priority` 경험적 지연율(`k=10`)과 정적 여섯 특징 경험적 지연율(`k=10·100`)을 셀의 학습 상수율로 수축한다. 분모 0은 학습 구간 셀별 상수로 대체한다. e·g 각각 검증 구간에서 Brier가 가장 낮은 기준선을 선택하고, 노출된 스트레스 구간에는 같은 기준선을 고정한다. 검증·스트레스 중 온라인 기준선은 해당 셀에서 5초 전에 이미 닫힌 과거 정답으로 갱신할 수 있다.
- 추가된 강한 **2단 계층 기준선**은 셀 ID를 빼고 여섯 제출 특징이 같은 학습 JOB의 지연율을 전역 학습율로 `k=10` 수축한 `p_static`을 먼저 만든다. 각 새 요청에서는 같은 셀·같은 여섯 특징의 직전 1시간 완료 사례 `n`, 지연 사례 `m`만 세어 `(m+20×p_static)/(n+20)`으로 계산한다. `p_static` 자체도 기준선에 포함한다. 이 기준선과 `k=20`은 e/g의 초기·확대 기준선 점수를 이미 열람한 **뒤** 개발 자료에서 선택했다. 따라서 e/g 검증·스트레스는 독립 효능 증거가 아니며, 앞선 31–53% 개선을 최종 성능으로 인용하지 않는다. f/h 사건 내용은 계속 열지 않고, 확대된 기준선에 대한 개발 게이트가 실패하면 외부 시험을 진행하지 않는다.
- 후보는 셀 ID와 절대 제출 시각을 입력하지 않는 `HistGradientBoostingClassifier` 하나로 고정한다. 입력은 제출 당시 여섯 특징, 과거 1시간 전체 지연율, 수축 강도 50의 과거 그룹 지연율, 두 집계 표본 수의 `log1p`다. 설정은 `max_iter=100`, `max_leaf_nodes=15`, `min_samples_leaf=200`, `learning_rate=0.05`, `l2_regularization=1`, `early_stopping=False`, `random_state=42`이며 검증 지표를 본 뒤 바꾸지 않는다. 사용 라이브러리·추출/평가 코드 해시와 학습 모델 해시를 결과에 남긴다.
- 주 지표는 **지연 양성의 Brier 손실**이다. 보조 지표는 로그 손실, 10% 경보 예산의 지연 재현율·정밀도, 10등분 보정 오차와 신뢰도 도표다. e·g 및 향후 f·h의 지표를 **각 셀별로** 보고한다. 전체 JOB 행 가중 점수 외에 일 단위 블록 부트스트랩 95% 구간과 `scheduler=0`·선행 의존성 없는 하위집단을 함께 보고한다. 두 셀을 합친 점수만으로 통과하지 않는다. 익명 user 키가 파생 CSV에 없어 사용자 균등 점수·사용자별 기여 상한은 이번 실험에서 확인할 수 없으며, 결과의 독립성 한계로 명시한다.
- 개발 게이트: 각 e·g 검증 셀에 관측 가능한 지연·비지연이 각각 1만 건 이상, 공식 검증된 전체 객체의 정상 사건 최소·최대 시각이 **하루 전체와 5초 판정 창을 덮는 날짜**가 4일 이상이어야 한다. 후보의 Brier가 그 셀에서 가장 강한 검증 기준선보다 **상대 5% 이상** 낮고, 10% 경보 재현율이 기준선보다 **1%포인트 초과** 나쁘지 않아야 한다. 둘 중 한 셀이라도 실패하면 f·h를 열지 않고 NO-GO로 기록한다. 기준선 자체는 유용한 설명자로 남을 수 있지만 AI-App 판단기에 채택하지 않는다.
- 개발 게이트 통과 시 **이 문서, 추출·특징·학습·평가 코드, 원본 검증 manifest, 선택한 모델/기준선의 매개변수, 지표·품질 게이트, 합성 테스트, 예상 시험 원본 메타데이터를 해시로 먼저 동결**한다. 그다음에만 f·h의 공식 원본을 수집한다. f·h의 결과를 보고 규칙을 바꾸거나 통과한 셀만 선택하지 않는다. 각 독립 셀은 지연·비지연 각각 1만 건과 최소 14개의 완전한 관측 날짜를 요구한다. 충족하지 않으면 해당 셀의 일반화 판정은 보류한다. 사용자 단위 기여도는 현재 CSV만으로 검사할 수 없으므로 이 품질 한계를 함께 보고한다.
- 최종 GO는 **f와 h 각각**에서 Brier가 그 셀의 가장 강한 동결 기준선보다 상대 5% 이상 낮고, 일 블록 부트스트랩의 `Brier_baseline − Brier_model` 95% 구간 하한이 0보다 크며, 10% 경보 재현율이 기준선보다 1%포인트 초과 나쁘지 않고, 10등분 보정 오차가 0.03 이하여야 한다. `scheduler=0`·의존성 없는 집단의 Brier도 기준선보다 나쁘지 않아야 한다. 어느 셀이든 품질·효과 기준을 실패하면 일반화 NO-GO로 보고하고 새 셀을 찾아 재시험 결과를 바꾸지 않는다. 통과해도 **범용 Borg JOB 시작 지연 신호**에 한해 GO다.

## 경희대 담당 기능과 해석 경계

이 확률은 배포 시 인프라 시작 지연 위험을 다루는 **보조 연구 신호**로만 관련된다. e·g 감사에서 5초 초과 비율은 `scheduler=1` batch job에서 e 54,830/59,268, g 15,943/52,775로 크게 달랐고, 선행 collection 의존성 있는 e 3,051건·g 69건은 전부 5초를 넘었다. `scheduler=0`만으로 제한해도 e 65,385/793,892, g 89,081/724,895건의 지연이 있어 셀별 드리프트와 시간순 기준선을 반드시 평가한다. 이 숫자도 탐색 감사에 한정된다.

Google collection 사건에는 AI 모델·GPU 종류·사용자 지연 SLO·VM 생성 요청/Ready·경희대 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 조치가 연결돼 있지 않다. [공식 trace 설명](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)에 최종 사용자 접근 패턴도 없다. 따라서 이 확률을 응용 형상 추천의 정답이나 배포·스케일 효과로 변환하지 않는다. 실제 서비스 판단에는 동일 AI-App의 발행 조치, 생성된 VM 및 replica Ready, 이후 사용자 요청 SLO를 결합한 로그가 필요하다.
