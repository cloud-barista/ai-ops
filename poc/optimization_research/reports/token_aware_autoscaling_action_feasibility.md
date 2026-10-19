# H100 llm-d 토큰 기반 오토스케일 실험: 조치·Ready·요청 결과 연결 감사

2026-09-29. **판정: sampled 목표 replica→Ready 전이의 기술적 분석에는 GO, 실제 발행된 `SCALE_OUT/IN` 조치와 사용자 결과의 학습·효과 검증에는 NO-GO.** 세 실행의 단계별 지연값을 개별 요청의 후속 결과로 취급하지 않는다.

## 원본과 출처

[작성자 GitHub 저장소의 고정 커밋](https://github.com/asm582/token-aware-autoscaling/tree/7e70bf5f69c773eb6f51f870c5d23cf41f0efb04)에서 H100-80GB, Qwen3-32B prefill/decode 실험 세 건의 텍스트 원본 33개와 수집 스크립트 (`../data/external/token_aware_autoscaling/benchmark/run-benchmark.sh`), `LICENSE`를 받았다. **35개, 260,916 B**이며, 개별 파일의 원본 URL·Git blob SHA-1·SHA-256·바이트 수는 [SOURCE.json](../data/external/token_aware_autoscaling/SOURCE.json)에 있다. 원본 저장소는 Apache 2.0 (`../data/external/token_aware_autoscaling/LICENSE`)이고, 실험 디렉터리 전체 42개 blob 중 9개 PNG 차트만 제외했다. 고정 커밋의 파일 목록에는 개별 요청 행이나 별도 발행 조치 로그가 없다. [출처 설명](../data/external/token_aware_autoscaling/SOURCE.md), [재현 스크립트](../scripts/audit_token_aware_autoscaling.py).

표의 `desired`는 두 표준 타임라인에서 Deployment `.spec.replicas`, `ready`는 `.status.readyReplicas`를 조회한 값이다. 수집 코드 (`../data/external/token_aware_autoscaling/benchmark/run-benchmark.sh`)는 약 15초마다 상태를 **조회**하고 그 경과 초(`elapsed_s`)를 적는다. 따라서 값이 바뀐 행의 시각은 첫 **관측 시각**이며 KEDA/HPA의 명령 발행 시각이 아니다. `prefill-heavy`는 작성자의 실험 보고서 (`../data/external/token_aware_autoscaling/experiments/2026-08-20-prefill-heavy/EXPERIMENT-REPORT.md`)에 따르면 초기 wrapper가 중단된 뒤 교체한 sampler의 부분 타임라인이다. 이 파일은 `epoch` 초와 `iso` 시각을 쓰며, 교체 sampler 자체의 코드는 원본에 없다. 그 `ready`의 수집 구현을 표준 sampler 코드로 독립 검증할 수 없다.

## 원본 CSV와 JSON에서 직접 센 값

| 실행 | 타임라인 | 시계·간격 | prefill 목표 ↑/↓ | decode 목표 ↑/↓ | 목표 ↑ 뒤 Ready 도달 관측 | 단계별 성공/실패 집계 |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| `staged-ramp` | 257행, 4,115초 | 상대 `elapsed_s`, 15–18초 | 4/4 | 2/2 | 6/6 | 2,482/8 |
| `prefill-heavy` | 160행, 2,668초 | UTC `epoch`, 16–18초 | 0/2 | 0/2 | 0/0 | 1,949/1 |
| `decode-heavy` | 359행, 6,346초 | 상대 `elapsed_s`, 17–23초 | 2/2 | 3/3 | 5/5 | 1,181/286 |
| **합계** | **776행** | 서로 다른 실행·시계 | **6/8** | **5/7** | **11/11** | **5,612/295** |

관측된 목표 변경은 합계 **26건(상향 11, 하향 15)**, Ready 수 변경은 **25건(상향 11, 하향 14)**이다. 상향 11건은 같은 역할의 목표 수가 다시 바뀌기 전에 Ready 수가 해당 목표에 도달한 사례다. 두 행에 찍힌 시각의 차이는 **176–257초, 중앙값 213초**다. 표준 수집기는 각 필드를 별도 `kubectl` 호출로 읽고 그 뒤에 행 시각을 기록하므로 필드별 조회 시각은 알 수 없다. 따라서 이 값은 *sampled target-to-Ready* 행 시각 차이이며, 실제 전이 시각의 엄밀한 상·하한은 계산할 수 없다. 원본의 발행→Ready 지연이나 AI 응용 VM 생성시간으로 해석하지 않는다. Ready는 Deployment의 상태 카운트이지 새 replica의 개별 요청 처리 준비 검사 결과가 아니다.

`prefill-heavy`는 첫 기록(2026-08-20 19:04:06 UTC)에 이미 prefill 목표/Ready가 4/4, decode가 3/2이다. 마지막 기록(19:48:34 UTC)은 prefill 2/2, decode 1/1이며 원본 보고서가 설명하는 초기 1→4 상향과 최종 2→1 구간이 이 CSV에는 없다. `decode-heavy`의 보고서는 타임라인을 345행이라고 설명하지만, 고정된 실제 CSV 헤더 제외 **359행**이다. 위 분모는 보고서 문장 대신 보존한 CSV에서 계산했다.

각 실행에는 `stage_0`–`stage_6_lifecycle_metrics.json` 7개와 실행 전체 요약 1개가 있다. 각 단계의 `successes.count`·`failures.count` 합은 전체 요약과 세 실행 모두 일치한다. JSON의 지연·처리량·실패 값은 **단계 집계와 분위수**이며, 요청 ID·시작/완료 시각이나 replica/action ID가 없다. `workload-used.yaml`에는 `per_request: true`가 설정돼 있으나, 고정 커밋에 개별 요청 산출물은 게시되지 않았다. 단계 시작의 절대 시각도 원본 JSON에는 없어 타임라인의 특정 목표 전이 뒤 어떤 개별 요청이 실패했는지 검증할 수 없다. 작성자 보고서의 scale-down 시 실패 해석은 저자 해석으로 남기고, 이 자료의 독립 검증 결과로 격상하지 않는다.

## 연구 사용 범위와 다음 데이터 게이트

이 작은 원본은 **샘플링된 목표 수와 Ready 수의 전이·관측 지연**, 로그 시계와 누락 구간 처리, 단계 집계의 일관성 검사에 쓸 수 있다. 상향 전이가 두 완전 타임라인에서 11건이고 모두 동일 H100/Qwen3-32B 배치 조건이므로 범용 준비시간 모델을 학습·검증할 만큼 독립 조건이나 반복이 없다. 단계 부하가 정책 조치를 유발하고 동시에 지연에 영향을 주며 고정 replica 비교 실행도 없어, 단계 지연 차이를 조치 효과나 대체 정책의 성능으로 학습할 수 없다.

경희대 `SCALE_OUT/IN` 판단 모델의 실행 결과 학습에는 동일 run/app의 **decision ID, 실제 제출·수락된 action ID와 발행 시각, 대상 replica/VM의 Ready 시각, 요청별 시작·완료·성공·지연**, 그리고 조치 없는/다른 정책 비교 실행이 더 필요하다. 이 자료의 11개 sampled 성장 전이는 그 계약을 대체하지 않는다. 더 가까운 공개 예약 사건 후보인 [Airflow 단일 실험](airflow_action_feasibility.md)은 별도 4.18 GB 원본과 PostgreSQL 복원이 필요해 이번 소형 감사에서는 보류했다.
