# PAX 전환 공백을 제외한 v2 전후 창: 적격성 결과

2026-09-28. **판정: NO-GO.** 노드 수가 바뀐 관측 공백을 성능 전후 창에서 제외하자 일부 사례를 확보했지만, 사전 GO 문턱인 10건·증가/감소 각 3건에 미치지 못했다. 이 게이트를 판단할 때는 `results.csv`의 YCSB 처리량·응답시간 값을 읽거나 비교하지 않았다. 이후의 별도 열람은 문서 끝에 기록했다.

## 사전 규칙과 출처

[v2 사전 규칙](pax_transition_gap_bracket_v2_protocol.md)을 결과 집계 전에 SHA-256 `8663b50f846517916985063ae7a88fe787364e6ac6d9bdcb600e1f6026bdf436`으로 고정했다. 이전 [v1 게이트](pax_transition_gate_findings.md)의 120초 전환 표본 간격 상한은 58건 전부를 탈락시켰다. v2는 그 간격에서 실제 전환 시각을 추정하지 않는다. 마지막 구 상태 관측 `t_old` 이전의 `[t_old−180초,t_old]`와 첫 신 상태 관측 `t_new` 이후의 `(t_new,t_new+180초]`를 사용하며, 사이의 미관측 공백에서 YCSB 결과를 비교하지 않는다.

입력은 [공식 PAX ZIP](https://zenodo.org/records/1136276) SHA-256 `db15cfa75f05d44020a3f5c4f6f4930cbb1c2518dd3884391917615cea52a23a`, MD5 `56cf6c11f79e0cffadeb2414784a789b`, 1,307,826 B다. ZIP CRC가 통과했고 9개 완전 실험 폴더를 모두 파싱했다. [v2 코드](../scripts/evaluate_pax_transition_gap_bracket_v2.py)는 동결된 v1 파서의 결과 시각 전용 함수를 사용한다. 합성 테스트에는 성능 셀을 숫자가 아닌 `OUTCOME_MUST_STAY_UNREAD` 문자열로 넣어, 적격성 코드가 시각만 읽어도 작동함을 확인했다.

## 단계별 적격 수

| 조건을 누적 적용한 뒤 | 전환 수 |
| --- | ---: |
| 원시 관측 노드 전환 | **58** (증가 30, 감소 28) |
| 전창 직전·후창 직후에 같은 상태의 노드 표본 존재 | 35 |
| 다른 노드 전환의 관측 구간이 창에 닿지 않음 | 35 |
| 양쪽 YCSB 시각: 창마다 ≥12개, 가장자리·간격 충족 | 35 |
| 양쪽 클라이언트 시각과 전환 공백 중 연속 관측 | 35 |
| 전후·공백 전체의 클라이언트 수가 전창 중앙값 ±10% | **6** |
| 서로 겹치는 적격 전후 창 제거 후 | **6** (증가 1, 감소 5) |

노드 변화 관측 공백은 최소 185.00초, 중앙값 186.71초, 최대 311.81초였다. 전체 58건 기준 사유를 중복 집계하면 양쪽 노드 상태 지지가 없는 사례 23건, 클라이언트 수 안정성 미충족 50건이다. 앞선 단계를 통과한 35건 중 29건이 클라이언트 수 조건에서 떨어졌다. 관측 시각 자체의 부족이 핵심 병목은 아니다.

최종 6건은 `Aggressive strategies/Conservative` 1건(증가), `Aggressive strategies/Normal` 4건(감소), `Validation/Change Architecture Proactive` 1건(감소)에 분포했다. 이 세 폴더의 기록 시간 범위는 겹치지 않지만, 폴더가 다르다는 사실은 통계적 독립성의 증거가 아니다. [요약 JSON](../artifacts/pax_transition_gap_bracket_v2/gate_summary.json)과 후보별 JSONL (`../artifacts/pax_transition_gap_bracket_v2/transition_candidates.jsonl`)에 모든 후보의 양쪽 시각 범위, 방향, 클라이언트 안정성 및 탈락 사유를 저장했다.

사전 규칙은 적격 10건 이상·3개 이상 비중첩 폴더·증가와 감소 각각 3건 이상을 요구한다. 현재는 폴더 수만 충족한다. 따라서 YCSB 전후 성능 비교는 진행하지 않는다. 노드 수는 온라인 Cassandra 상태의 관측값이며 실제 증감 명령과 action ID가 없다. 여기서 정책의 인과 효과나 AI-App GPU VM 스케일링 성능을 주장할 근거는 없다.

재현: `PYTHONPATH=/home/researcher/ai-mcmp/research python3 poc/optimization_research/scripts/evaluate_pax_transition_gap_bracket_v2.py`. v2 코드 SHA-256 `d2b08e22841c2edd3f50d9372bf9e29c09f01f387ba36aa45ca273c0a36c502d`; v1 파서 의존성 SHA-256 `d5a958d4bb687da8e1c73779eb883179de74e2c4918e4dcfeddc48ced1c5f562`. 새 테스트와 v1 테스트 합계 13건 통과. 요약 JSON SHA-256 `6e8657de20269c796ff08979dc118a04b12b41083b5c3b1d4a286c0b30a012ec`; 후보 JSONL SHA-256 `ca44579cc112420eb89b6c7e60d2fe4387a927b0596497afdf1feec7f7942cf2`.

**후속 열람 기록(2026-09-28):** 이 v2 게이트가 NO-GO로 종료된 뒤, 별도 재현성 점검에서 `results.csv`의 처리량·`readRT` 수치를 읽고 전후 평균을 임시 계산했다. 기존 v2의 동결 코드·적격성 결과와 판정은 바꾸지 않았고 그 임시 계산 결과는 연구 산출물로 유지하지 않는다. 다만 이 시점 이후 PAX 결과값을 열지 않은 독립 시험이라고 주장할 수 없다. 같은 PAX 원본에서 새 결과 의존 가설을 고를 때는 후향 탐색으로 표시해야 한다.
