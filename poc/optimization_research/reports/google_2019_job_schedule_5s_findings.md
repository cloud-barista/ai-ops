# Google 2019 JOB 시작 지연 위험: 학습은 가능, 현재 모델은 미채택

작성일: 2026-09-29. [공식 v3 사건 정의](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)의 `JOB SUBMIT=0` 후 `SCHEDULE=3`이 **5초 안에 없는지**를 e·g 셀에서 학습했다. `SCHEDULE`은 collection 실행 시작이며 VM 생성 완료나 AI-App Ready가 아니다. [실험 규약](google_2019_start_delay_protocol.md)은 제출 시점 여섯 특징과 과거 1시간에 정답이 이미 확정된 JOB만 입력으로 허용한다.

## 판정

**NO-GO: 현재 HistGradientBoosting 모델을 경희대 배포·스케일링 판단의 신호로 채택하지 않는다.** 첫 비교에서는 모델의 검증 Brier가 최근 `scheduler×class×priority` 지연율보다 e에서 31%, g에서 53% 낮았다. 그러나 그 기준선은 제출 당시 `alloc_collection_id`·부모·선행 의존성을 사용하지 않았다. 전체 여섯 특징의 학습 빈도표를 같은 셀의 **이미 완료된** 최근 1시간 지연율로 보정한 단순 2단 기준선을 추가하자 e에서 기준선이 모델보다 좋았고, g의 모델 이득도 검증 기준인 상대 5%에 못 미쳤다. e·g 검증 구간을 본 뒤 기준선을 강화했으므로 모든 e·g 점수는 **탐색적 개발 결과**다. 아직 열지 않은 f·h 셀은 개발 게이트 실패에 따라 수집·개봉하지 않았다.

| 노출 셀·기간 | 총 JOB / 5초 초과 JOB | HGB Brier | 최강 단순 기준선 Brier | 모델의 상대 Brier 개선 | 10% 경보 재현율 HGB/기준선 | 일 블록 95% 차이 구간¹ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| e, day 20–24 검증 | 125,106/19,383 | 0.06030 | **0.05961** | **−1.16%** | 54.55%/56.77% | −0.00193 ~ 0.00057 |
| g, day 20–24 검증 | 127,998/16,934 | **0.03925** | 0.04042 | **2.87%** | 66.90%/61.46% | −0.00013 ~ 0.00263 |
| e, day 25–30 노출 스트레스 | 169,992/16,403 | 0.04353 | **0.04341** | −0.26% | 64.84%/64.17% | −0.00043 ~ 0.00020 |
| g, day 25–30 노출 스트레스 | 154,161/20,117 | **0.04070** | 0.04388 | 7.26% | 65.32%/59.86% | 0.00166 ~ 0.00503 |

¹ `Brier_기준선 − Brier_모델`의 날짜 블록 부트스트랩 2,000회 구간이다. 검증은 셀마다 원본 사건의 **추적 시간 경계가 day 20–24 전체를 덮는 5일**을 포함한다. 5일만 재표본한 구간도 탐색적 불확실성 표시다. 스트레스 구간의 기준선 종류와 수축 강도는 **검증 구간에서 선택한 그대로** 사용했다. 스트레스 점수도 별도 독립 검증은 아니다. 고정 확률 10등분 보정 표, ECE, 로그 손실, 기본 스케줄러·의존성 없는 하위집단 결과는 [원 결과](../artifacts/google_2019_job_schedule_5s_model_v3/results.json)에 있다.

## 데이터와 비교 방법

[e 원본 메타데이터](../data/external/google_cluster_2019_e/SOURCE.md)와 [g 원본 메타데이터](../data/external/google_cluster_2019_g/SOURCE.md)가 기록한 공식 단일 JSON gzip 전체를 [추출기](../scripts/build_google_2019_job_schedule_5s.py)로 읽었다. 원본 사건 행은 시간순이 아니므로 임시 SQLite로 `(cell, collection_id)`를 접합했고, 원본·추출 코드·CSV 해시와 결측/검열 수를 [e 감사](../artifacts/google_2019_job_schedule_5s_v1/cell_e/metadata.json)·[g 감사](../artifacts/google_2019_job_schedule_5s_v1/cell_g/metadata.json)에 남겼다. 최종 라벨은 e **853,210건 중 120,215건**, g **777,709건 중 105,026건**이 5초 초과다. 5초 판정 창이 끝나기 전 추적이 종료한 JOB은 셀당 1건씩 제외했다. `QUEUE`·`ENABLE`, 이후 업데이트·자원 사용량·실제 시작 시각은 입력에 넣지 않았다. 5초 뒤 중복·결측·충돌 사건이 이미 확정된 표본을 제외하지 않는지 합성 테스트했다.

학습은 두 셀의 day 0–19에서 e **556,440건**, g **494,694건**을 사용했다. [평가기](../scripts/evaluate_google_2019_job_schedule_5s.py)의 모델은 여섯 제출 특징, 같은 셀의 최근 1시간 전체·`scheduler×class×priority` 완료 지연율, 표본 수를 입력받는 `HistGradientBoostingClassifier` 한 개다. 모델 설정은 [규약](google_2019_start_delay_protocol.md)에 고정했다. 학습·검증 경계에는 5초 라벨 확정 시간을 적용했고, 온라인 집계는 `t_i+5초<t`인 이전 JOB만 썼다. 검증 기간은 day 20–24, 개발 스트레스는 day 25–30이다.

가장 강한 단순 기준선은 두 단계다. 먼저 e·g **학습 기간만** 사용해 같은 여섯 제출 특징의 전역 지연율을 학습 전역률로 10건 수축한다. 이 `p_static`을 각 셀의 같은 여섯 특징에 대한 직전 1시간 **완료된** JOB 지연율과 `(최근 지연 건수+20×p_static)/(최근 완료 건수+20)`으로 합친다. 검증 e·g 양쪽에서 이 기준선이 가장 낮은 Brier를 냈다. `has_alloc_collection`에 따른 검증 지연율은 e에서 35.9% 대 12.4%, g에서 60.3% 대 3.0%로 갈려, 이 특징을 놓친 초기 기준선이 약했다는 가설과 일치한다. 이는 **원인 효과**가 아니라 기록된 JOB 종류의 연관성이다.

## 재현과 해석 경계

```bash
cd /home/researcher/ai-mcmp/research
python3 scripts/build_google_2019_job_schedule_5s.py --cell e --input data/external/google_cluster_2019_e/collection_events-000000000000.json.gz --output-dir /tmp/google_job_schedule_e_new
python3 scripts/build_google_2019_job_schedule_5s.py --cell g --input data/external/google_cluster_2019_g/collection_events-000000000000.json.gz --output-dir /tmp/google_job_schedule_g_new
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 scripts/evaluate_google_2019_job_schedule_5s.py --cell-e-dir /tmp/google_job_schedule_e_new --cell-g-dir /tmp/google_job_schedule_g_new --output-dir /tmp/google_job_schedule_model_new
python3 -m pytest -q tests/test_build_google_2019_job_schedule_5s.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 -m unittest discover -s tests -p test_evaluate_google_2019_job_schedule_5s.py -q
```

[최종 개발 결과](../artifacts/google_2019_job_schedule_5s_model_v3/results.json)는 e/g 원본·빌더·CSV 해시를 재검사한 실행의 결과다. 저장 모델 (`../artifacts/google_2019_job_schedule_5s_model_v3/model.joblib`) SHA-256은 `931950eb1ddc4e9e42d2978432cfeef6fb16e8b19ced1b77df649f689cc23088`; v1·v2·v3의 **모델 바이트는 동일**하고 기준선만 강화했다. 최종 평가기 SHA-256은 `b0fac62c5c8f680fc54d4c6c230404946e76fdcc96549f45eb939ff730d2849b`, 규약 SHA-256은 `ce5d652ec808de0ecde10f7c6334e1348e983145c0543646d7975d53fd341461`이다. 같은 경로의 산출물이 이미 있으면 새 출력 디렉터리를 지정해야 한다.

이 연구에는 익명 user 키도 파생 CSV에 남기지 않아 사용자별 표본 집중도를 평가하지 못했다. 더 중요하게, collection 사건에는 AI 모델·GPU 요구량, VM 생성/Ready, 사용자 요청 SLO, 경희대의 `DEPLOY`/`SCALE_OUT` 같은 발행 조치와 결과가 연결돼 있지 않다. 따라서 분류기를 VM 형상 추천이나 실제 스케일 효과 추정으로 해석할 수 없다. 현재 결론은 이 범용 Borg 시작 지연 과제에서 **복잡한 모델의 추가 가치가 강한 단순 기준선에 비해 검증되지 않았다**는 것이다. AI-MCMP에는 기존 SQLite 요구 신호의 요청 결속·보류 계약을 유지하고, 실제 배포 판단 학습에는 발행 조치→VM/replica Ready→사용자 SLO의 연결 로그를 수집하는 방향이 적절하다.

추가 후보인 `UPDATE_PENDING`도 e/g 원본 전체에서 [압축 원본 해시를 확인하는 집계기](../scripts/audit_google_2019_update_pending.py)로 점검했다. JOB의 원시 `SUBMIT` 사건은 e **861,155건**, g **784,355건**이고, JOB `UPDATE_PENDING`은 각각 **821건**, **103건**으로 모두 서로 다른 제출 JOB ID에 연결됐다. e의 업데이트 821건 중 **744건(90.6%)**이 day 13–24에 몰렸다. [공식 사건 정의](https://github.com/google/cluster-data/blob/master/clusterdata_trace_format_v3.proto)는 이를 대기 중인 collection의 스케줄링 등급 또는 자원 요청 변경이라고 설명한다. 이 사건은 AI-App 코드 업데이트 필요성이나 업데이트 후 사용자 성능을 나타내지 않으며, 셀·시간 분포도 크게 달라 경희대 `UPDATE` 판단의 지도학습 정답으로 채택하지 않는다. 위 원시 제출 사건 수는 5초 라벨 빌더가 특수 시각과 검열 표본을 제외한 유효 JOB 수와 구분한다. 재현 명령은 연구 루트에서 `python3 scripts/audit_google_2019_update_pending.py`다.
