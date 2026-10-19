# 기존 SQLite만으로 새 응용 요구사항 분석 모델을 학습할 수 있는가

작성일: 2026-09-28. 이 문서는 기존 연구와 **다른 지도학습 문제**를 찾기 위한 읽기 전용 적격성 감사다. 모델 학습이나 운영 인터페이스 변경은 수행하지 않았다.

## 판정: NO-GO

현재 7개 원본 SQLite에는 등록 응용이나 사용자 요청으로부터 **필요 CPU·RAM·GPU 종류/수·VM 노드 수를 해석한 정답**이 없다. 동일 SQLite에서 새 요구사항 분석기를 지도학습하고 독립적으로 검증할 새 라벨을 찾지 못했다. 배치 지연·처리량, 관측 GPU 예약 피크, 로컬 모델 적재시간은 학습 가능하지만 각각 이미 [성능 예측](../artifacts/performance_v1/experiment_report.md), [VRAM 피크](vram_peak_findings.md)·[상한](vram_upper_findings.md), [적재시간](load_time_findings.md)에서 다뤘다. 이들을 요구 CPU/GPU/VM의 정답으로 다시 이름 붙이지 않는다.

**적격성 게이트:** 새 지도학습 과제는 (1) 실행 전 등록 정보/사용자 요구에서 얻는 입력, (2) 기존 성능·피크·적재시간과 구별되는 관측 정답, (3) 한 앱을 통째로 제외해도 검증 가능한 다수 앱·작업 종류와 정답의 유효한 변동, (4) 실제 인프라 요구를 주장하려면 해당 자원 제한/후보에서의 성공·실패 또는 사용자 SLO 결과를 모두 만족해야 한다. 현재 (2)와 (4)가 0건이며, 생성형 모델은 로컬 호스트에 1종뿐이므로 (3)도 충족하지 않는다. 따라서 점수 문턱을 정하거나 훈련 데이터를 만들 단계로 진행하지 않는다.

## 확인한 자료와 누출 위험

고정 원본 추출 (`../data/audit_v1/measurements.parquet`)의 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. [추출 코드](../scripts/audit_data.py)는 `/path/to/aiops/runs/khu-hf-*/executions.sqlite3` 7개를 읽기 전용으로 열어 `collection_runs`, `execution_measurements`, `node_samples`를 감사한다. 스키마상 전체 9,591실행, 학습 가능 완료 실행 9,445개, 고유 모델 11종, `model_id`·revision·batch·길이·동시성·dataset 기준 작업 그룹 671개다. 행이나 반복 횟수를 독립 AI-App 수로 취급하면 안 된다.

| 후보 입력/정답 | 출처·관측 시점 | 요구사항 분석에 대한 판단 |
| --- | --- | --- |
| `model_id`, revision, batch, `max_length`, `request_concurrency`, `device`, `cpu_core_limit`, `replica_count` | 실험 프로필에 명시. 마지막 세 값은 연구자가 선택한 **실행 형상**이다. | 구조화된 등록·작업 정보와 실험 후보로는 사용할 수 있다. 원하는 인프라의 정답이나 자연어 해석 라벨은 아니다. `cpu_core_limit`은 프로세스 affinity다. |
| `target_throughput_rps`, `latency_slo_ms`, `load_condition` | 수집기 프로필 생성 코드 (`/path/to/aiops/khu_policy_agent/live_collector.py:714`)의 상수·식에서 생성. | 사용자 요청에서 추출한 SLO가 아니다. 수집 목표를 `요구사항 추출 정답`으로 학습하면 생성 규칙을 외우게 된다. |
| `result_json.workload_task`, `input_encoding`, `model_parameter_bytes` | 실행 **결과** JSON에 기록. 작업 종류는 모델별로 일정하지만 파라미터 **바이트 수는 실행 장치·dtype 및 replica 합산에 따라 바뀐다**. | 실제 등록 서비스가 실행 전 동일 정보를 제공할 때만 입력으로 인정할 수 있다. 예를 들어 Qwen 단일 replica는 CPU `1,976,131,072 B`, CUDA `988,065,536 B`였다. 결과 JSON 전체나 대상 실행의 메모리·지연을 입력하면 누출된다. |
| `latency_p95_ms`, `examples_per_second`, `gpu_memory_reserved_bytes`, `load_seconds` | 대상 벤치마크 종료 후 결과. 처리량은 샘플/초이며 요청/초는 batch로 나눠야 한다. | 형상 성능·관측 피크·로컬 적재시간의 라벨은 되지만 신규 요구사항 정답은 아니다. 배치 p95도 실제 사용자 응답 p95가 아니다. |
| `max_rss_kib`, 성공/실패, `node_samples` | 프로세스 RSS 누적 고수위, 실행 결과 및 노드 계측. | RSS는 장수명 프로세스의 이전 실행 영향을 받는다. GPU 성공 7,985건에서 OOM 0건이며 실패 10건은 모델 파일 `OSError`다. 최소 VM RAM/VRAM이나 이종 GPU 호환성을 판정할 음성 사례가 없다. [메모리 감사](memory_feasibility_review.md), [RSS 감사](next_research_idle_return_feasibility.md) |

원본에 응용 설명 텍스트, 사용자가 고른 인프라 요구량, 배포 승인, VM 변경 결과, CSP 가격은 없다. 별도 lifecycle SQLite의 `oracle_action`은 정적 GPU 복제 실험을 짝지은 파생 라벨이며 후보의 실행 후 GPU 사용률 누출과 처리량 단위 오류가 있다. [별도 감사](untapped_signal_audit.md) 때문에 새 독립 정답으로 합치지 않는다.

## 겉보기 대안의 표본·검증 한계

- **GPU가 필요한지 분류:** 주 `khu-hf-local-exhaustive`에서 CPU affinity 2·단일 replica로 정확히 같은 작업을 CPU와 GPU 모두 측정한 경우는 **64개 작업·8개 encoder 모델**이다. 생성된 목표를 배치 p95와 `examples_per_second / batch_size`에 적용하면 CPU만 충족 **0**, GPU만 충족 **4**, 둘 다 충족 **8**, 둘 다 실패 **52**다. 이는 사용자 요구가 아니고, 같은 CPU/GPU 성능 측정을 사후 비교한 **정적 후보 적합성**이다. 기존 성능 예측·추천 연구와 중복되며 GPU 필요성의 실제 운영 정답이 아니다.
- **작업 유형 분류:** `workload_task`의 완료 행은 encoder 추론 8모델과 causal LM 3모델이다. causal LM 중 로컬 `icns`에는 Qwen **1모델**, 원격 `ubuntu216`에는 Pythia·TinyLlama **2모델**뿐이다. 모델 ID나 수집기 프로필로 정해진 라벨을 외워도 새 응용 설명을 해석한 성과가 아니다. 동일 모델·형상을 두 호스트에서 성공 비교한 쌍도 없다.
- **최소 메모리·노드 수:** GPU 종류 두 개가 서로 다른 모델 집합에서 사용됐고 용량 경계 근처의 실행 실패가 없다. `replica_count`는 같은 호스트의 독립 GPU 프로세스 수이며 VM 노드 수가 아니다. 관측 피크 상한과 로컬 적재시간의 기존 모델은 특정 하드웨어에서 관측되는 보조 신호일 뿐, 최소 요구량이나 Scale-out 결과를 검증하지 않는다.
- **여러 요청 조건의 공통 형상:** 기존 [사전 적격성 실험](workload_envelope_gate_findings.md)은 원본 생성 SLO에서 비자명 묶음이 18개 중 3개에 그쳐 동결된 `≥4개` 게이트에서 멈췄다. 이를 새 요구사항 분석 정답으로 재활용하지 않는다.

## 지금 가능한 경희대 작업과 재개 조건

현재 SQLite만으로는 **모델을 더 학습시키는 것보다** 요청·등록 정보의 결정 시점과 단위를 명시한 구조화 요구사항 입력 계약을 만드는 일이 타당하다. `model_id/revision`, 작업 종류, batch, 입력 길이, 동시성, 실제 사용자가 제공한 지연/처리량 목표를 각각 출처와 함께 보관한다. 모델 크기는 등록 시 측정 방법·dtype을 함께 제공해야 한다. 정보가 없거나 훈련 지원 범위를 벗어나면 요구 CPU/GPU/메모리/노드 수를 채우지 않고 `ABSTAIN`한다. 성능 추정치는 기존 [연구→배포 명세 경계](research_to_deployment_spec_boundary.md)대로 별도 증거로 전달한다.

새 학습을 재개하려면 **실제 등록 입력과 사용자 목표**, 동일 응용을 여러 자원 제한/VM 형상에서 실행한 성공·실패와 사용자 요청 단위 SLO, 결정 시각 이전 특징과 결과의 `available_at`, 최소 두 작업 종류에서 다수의 신규 앱을 확보해야 한다. 사전 평가 규약은 앱/모델 전체 제외와 작업 종류 공동 제외를 주 분할로 두고, 요구 필드의 정확 추출률·단위 오류율, 정답 형상의 위반 승인율·보류율을 보고해야 한다. 현재 수치에는 그런 정답이 없으므로 점수나 성능 향상을 보고하지 않는다.

재현용 읽기 전용 집계: 추출 파일에서 `eligible`을 선택하고, CPU/GPU 쌍은 `campaign='khu-hf-local-exhaustive'`, `cpu_core_limit=2`, `replica_count=1`으로 제한한다. `workload_group`으로 pivot한 뒤 두 장치의 `(latency_p95_ms <= latency_slo_ms) & (examples_per_second / batch_size >= target_throughput_rps)`를 비교한다. 스키마는 원본 DB에서 세 테이블 각각에 `PRAGMA table_info`를 실행해 확인했다.
