# PAX 온라인 노드 전환의 전후 관측 적격성 결과

2026-09-28. [공식 PAX 자료](https://zenodo.org/records/1136276)는 Cassandra 온라인 노드 수, YCSB 결과, 연결 클라이언트 수를 함께 제공한다. ZIP 안의 README (`../data/external/pax_autoscale_feasibility/extracted/PAX/README.txt`)에 따르면 `runningVMs.csv`의 `nodes`는 **온라인 상태로 관측된 Cassandra 노드 수**다. 이 파일에는 노드 증감 **명령 시각이나 action ID가 없다**. 따라서 여기서 센 전환은 실행 명령이 아닌 상태 표본의 변화다.

## 사전 규칙과 원본 검증

[사전 게이트 규칙](pax_transition_gate_protocol.md)을 전환 건수 계산 전에 SHA-256 `492c510798a93260ede5d3f20662a7aa25a3dc33238a3230c4d8164795af4d2c`으로 고정했다. 각 전환에서 첫 새 노드 수 관측 시각 `t`를 잡고, 직전 관측과의 간격 ≤120초, 다른 전환과 겹치지 않는 ±3분 창, 전후 YCSB 시각 12개 이상, 클라이언트 시각 12개 이상과 전창 중앙값 대비 모든 클라이언트 수 ±10%를 요구했다. 최종적으로 10개 이상 전환·3개 이상 실험 폴더·증가와 감소 각 3개 이상이어야 제한적 전후 관측 비교를 허용한다.

공식 Zenodo 메타데이터의 `PAX.zip` 크기 **1,307,826 B**, MD5 `56cf6c11f79e0cffadeb2414784a789b`을 로컬 원본과 대조했고, ZIP 전체 CRC도 통과했다. ZIP SHA-256은 `db15cfa75f05d44020a3f5c4f6f4930cbb1c2518dd3884391917615cea52a23a`이다. [분석 코드](../scripts/evaluate_pax_transition_gate.py)는 `results.csv`에서 `datetime`만 추출한다. 처리량·응답시간 값은 파싱·저장·비교하지 않았다.

## 적격성 집계

| 검사 | 건수 |
| --- | ---: |
| 세 CSV가 모두 있는 실험 폴더 | 9 |
| 원시 온라인 노드 수 전환 | **58** (증가 30, 감소 28) |
| 직전 노드 표본과 새 표본 사이 ≤120초 | **0/58** |
| 다른 전환이 ±3분 내에 없음 | 58/58 |
| 전후 YCSB 시각에 각각 ≥12개와 창 끝 범위·간격 충족 | 58/58 |
| 전후 클라이언트 시각에 각각 ≥12개 | 58/58 |
| 전후 클라이언트 수가 전창 중앙값 ±10% 안에 유지 | 21/58 |
| 모든 사전 조건을 충족한 전환 | **0/58** |

전환 직전 노드 표본과 첫 새 표본 간격은 **최소 185.00초, 중앙값 186.71초, 최대 311.81초**였다. 모든 58건이 120초 상한을 넘는다. 각 실험 안의 연속 전환 간격은 최소 564.54초로, ±3분 창에 다른 전환이 들어오는 경우는 없었다. 9개 폴더의 전체 기록 시간 범위가 서로 겹치는 쌍은 0개였다. 폴더별 건수와 각 후보의 탈락 사유는 [요약 JSON](../artifacts/pax_transition_gate_v1/gate_summary.json)과 후보 JSONL (`../artifacts/pax_transition_gate_v1/transition_candidates.jsonl`)에 있다. 다른 날짜·폴더라는 사실만으로 워크로드와 설정의 통계적 독립성까지 입증되지는 않는다.

첫 파서 실행은 9개 `clients.csv` 모두의 **맨 끝 빈 행 하나**를 잘못된 행으로 처리해 실험 9개를 모두 제외했다. 같은 9개 `results.csv`에도 맨 끝 빈 행 하나씩 있고, 내부 빈 행은 없음을 ZIP에서 확인했다. [첫 실패 기록](../artifacts/pax_transition_gate_v1/first_parse_failure.json)을 보존하고, 내용이 있는 잘못된 행과 내부 빈 행은 계속 오류로 처리하면서 **끝 빈 행만 무시**하도록 파서를 고쳤다. 이 보정은 사전 게이트 조건을 바꾸지 않았다. 합성 테스트 5개가 통과했다.

자료 형식 점검 중 `results.csv` 각 실험의 첫 줄을 표시하는 명령이 첫 번째 처리량 값 하나도 함께 출력했다. 이 출력은 사전 조건 선택, 전후 차이 계산, 코드나 산출물에 사용하지 않았다. 사전 규칙은 이 노출 이후에 고정했으며, 실제 적격성 실행은 결과 값 대신 시각만 사용했다.

**판정: NO-GO.** 현재 `t` 기준 `[t-180초,t)` 전창은 모든 전환에서 마지막 이전 상태 관측보다 늦게 시작한다. 실제 전환이 언제 일어났는지 알 수 없으므로 이 창을 확실한 ‘전’ 상태로 해석할 수 없다. 결과 차이나 개선 효과를 계산하지 않았다. 향후 비교를 시도한다면 **마지막 이전 상태 표본 이전에 끝나는 전창**과 **첫 새 상태 표본 이후에 시작하는 후창**을 새 규칙으로 결과 열람 전에 고정해야 한다. 실제 명령 시각·실행 결과와 AI-App 사용자 SLO가 없는 한 이 데이터로 AI-App GPU VM 스케일 정책의 효과를 검증할 수 없다.

재현성: 분석 코드 SHA-256 `d5a958d4bb687da8e1c73779eb883179de74e2c4918e4dcfeddc48ced1c5f562`; 요약 JSON SHA-256 `776d4dab586d108b8e5d958de6dd7f602c9c9ea6df7cc17c534892e7d3eaa7c3`; 후보 JSONL SHA-256 `fac017663d658fe14598455de8eb7fae528596706cd1dac4959fce287800d1cf`; 첫 실패 기록 SHA-256 `90bedf916a955b0bebfa2c768e2b7bb2968c7246cdd5d011cedf3700b8b81be5`. 실행: `python3 poc/optimization_research/scripts/evaluate_pax_transition_gate.py`, 테스트: `PYTHONPATH=/home/researcher/ai-mcmp/research pytest -q poc/optimization_research/tests/test_pax_transition_gate.py`.
