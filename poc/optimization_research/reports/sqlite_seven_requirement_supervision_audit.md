# 원본 SQLite 7개의 응용 요구사항 학습 정답 재점검

2026-09-29. `/path/to/aiops/runs/khu-hf-*/executions.sqlite3` 7개를 SQLite `mode=ro`·`query_only=ON`으로 다시 열어 테이블과 열을 확인했다. 세 물리 테이블은 `collection_runs`, `execution_measurements`, `node_samples`다. 실행은 **9,591건**, 성공은 **9,581건**, 기존 감사의 학습 적격 행은 **9,445건**이다. 고정 추출 파일 (`../data/audit_v1/measurements.parquet`)의 SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`도 재확인했다. 성공 실행의 `result_json` 9,581개, `node_summary_json` 9,591개, 수집 설정 `config_json` 154개의 키 구조를 조사했다. [기존 스키마·라벨 감사](sqlite_requirement_analysis_new_task_feasibility.md)와 결과가 같다.

| 지도학습 목표 | 실제 입력·정답 | 판정 |
| --- | --- | --- |
| 사용자 요청 **텍스트 → CPU/GPU/메모리/노드 요구 추출** | 원문·응용 설명·사람이 확인한 요구 필드가 세 테이블과 조사한 JSON 키에 없다. `profile_name`과 `dataset`은 실험 설정이다. | **불가**: 입력과 감독 정답이 모두 없다. |
| 실행 전 모델/작업 정보 → `workload_task` | 성공 행에는 `inference` 7,360건과 `causal_lm_forward` 2,221건이 있지만, 이 값은 결과 JSON에 기록된다. 모델·수집 프로필이 작업 종류를 사실상 정한다. | **형식상 가능, 새 요구 해석 모델로 부적합**: 모델 ID를 외우는 분류와 구별하기 어렵다. |
| 등록 정보 → 사용자가 요구한 지연·처리량 SLO | `latency_slo_ms`, `target_throughput_rps`는 수집기 프로필의 상수·공식 (`/path/to/aiops/khu_policy_agent/live_collector.py:714`)에서 나온다. 실제 사용자 요청 기록은 없다. | **불가**: 생성 규칙의 재현은 사용자 요구 추출이 아니다. |
| 실행 전 모델/배치/입력 조건 → 관측 피크·적재시간·배치 성능 | GPU 예약 피크, `load_seconds`, 배치 p95, 샘플/초는 성공 실행에서 관측된다. [피크](vram_peak_findings.md), [적재시간](load_time_findings.md), [성능](../artifacts/performance_v1/experiment_report.md)으로 이미 연구했다. | **제한적으로 가능·기존 과제**: 같은 장치의 관측 신호를 예측할 수 있다. 최소 필요 형상 정답은 아니다. |
| 등록 정보 → **최소 CPU 코어·VM RAM·GPU 종류/수·노드 수** | `cpu_core_limit`과 `replica_count`는 연구자가 고른 프로세스 affinity·GPU 프로세스 수다. VM 노드 수·RAM 제한·이종 GPU 대조 실험이 없다. GPU 성공 7,985건에 OOM 0건이며 실패 10건은 모델 파일 `OSError`다. `max_rss_kib`는 장수명 프로세스의 RSS 고수위다. | **불가**: 최소 안전 자원의 성공/실패 경계가 없다. [메모리 감사](memory_feasibility_review.md) |

행 9,591개는 독립 응용 9,591개가 아니다. 고유 Hugging Face 모델은 **11종**, 모델·revision·입력 조건으로 묶은 작업은 **671개**이고 같은 모델의 자원 후보·반복·캠페인이 재등장한다. 로컬 9모델과 원격 2모델은 성공한 동일 모델·형상 비교로 연결되지 않는다. 행 무작위 분할이나 캠페인만 분리한 점수는 새 앱 일반화를 검증하지 못한다. 모델 전체 제외가 최소 평가 단위지만 생성형 모델 및 호스트별 지원량도 부족하다. `result_json`, 실행 후 `node_summary_json`, 대상 후보의 `running` GPU 계측을 등록 시점 특징에 넣으면 정답이 누출된다. 파생 lifecycle DB의 `oracle_action`도 같은 정적 실행을 재포장한 값이며 독립 정답이 아니다. [파생 DB 감사](untapped_signal_audit.md)

**다음 최선 데이터:** 실제 AI-App 등록 시점의 요청 원문과 사람이 확인한 구조화 요구·SLO를 같은 `application_id`/버전으로 기록하고, 모델 메타데이터의 수집 시각을 남긴다. 같은 앱을 여러 CPU·RAM 제한·GPU 종류/수·노드 형상에서 실행해 준비 완료, 실패/OOM, 요청별 지연·처리량을 연결해야 최소 충족 형상을 정할 수 있다. 앱/모델을 통째로 제외한 평가와 실제 요청 시각 기준 특징 차단이 필요하다. 현재 SQLite만 사용한다면 새로운 형상 지도학습을 열지 않고, 이미 검증한 관측 신호를 출처·지원 범위가 붙은 연구 증거로 유지한다.
