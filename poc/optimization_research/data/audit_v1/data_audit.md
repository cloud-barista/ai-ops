# SQLite 데이터 진단

- 추출 시각: 2026-09-28T02:14:08.265860+00:00
- 원본 행 수: 9,591
- 동일 실행 ID 중복 제거: 0
- 성공·완료 회차·유효 타깃을 만족하는 행: 9,445
- 응용/입력 조건 그룹: 671

| DB | 전체 | 학습 후보 | 모델 종류 | 누락 before |
| --- | ---: | ---: | ---: | ---: |
| khu-hf-cpu-scaling-local | 240 | 240 | 1 | 0 |
| khu-hf-diverse-cpu-matrix | 210 | 192 | 8 | 0 |
| khu-hf-live | 2640 | 2640 | 8 | 0 |
| khu-hf-local-exhaustive | 4856 | 4856 | 9 | 0 |
| khu-hf-local-novel-matrix | 176 | 176 | 9 | 0 |
| khu-hf-local-space-filling | 384 | 384 | 9 | 0 |
| khu-hf-remote-216 | 1085 | 957 | 3 | 0 |

## 해석

- 원본은 mode=ro + query_only + 읽기 트랜잭션으로 추출했다. DB별 일관된 뷰이며 DB 간 동시 시점은 보장하지 않는다.
- 추출 행을 Parquet로 고정하고 해시를 기록했다. node_samples 전체는 복사하지 않았고 phase 건수와 before 존재 여부만 진단했다.
- latency_p95_ms는 배치 단위 지연이다. 다중 replica에서는 replica별 p95의 최댓값이다.
- examples_per_second는 처리 샘플/초이며 다중 replica에서는 개별 처리량의 합이다. 온라인 요청/초 또는 생성 token/초가 아니다.
- max_rss_kib는 프로세스 RSS 고수위이며 replica별 합산된다. 최소 필요 VM 메모리의 정답으로 사용하지 않는다.
- 같은 조건의 반복 측정은 중복 실행과 다르다. 반복은 보존하되 평가에서는 workload 그룹을 동일 가중한다.
- 중단/실패 회차는 보존하되 첫 회귀 실험에서 제외한다. 실패 원인은 audit.json에 기록했다.
- 파생 lifecycle/합성 DB는 첫 학습에서 제외한다. 자연어 요구 분석, 실제 UPDATE/스케일 전환 성능은 이 실험의 평가 대상이 아니다.
- 조건 그룹은 같은 모델·revision·batch·길이·동시성·dataset을 묶는다. CPU/GPU·replica 선택과 반복 회차가 분할을 넘어가지 않게 한다.
- 상세 출처, 교차 캠페인 조건 겹침, 측정값 불일치, 장치 정보는 audit.json에 저장했다.
