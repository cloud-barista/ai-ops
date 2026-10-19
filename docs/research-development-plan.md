# AI-MCMP 경희대 학습 기반 연구·개발 계획

작성일: 2026-09-28. 이 문서는 최초 연구 계획을 보존한다. 이후 완료한 진단·학습·공개 데이터 평가의 현재 결론은 [종합 결과](../poc/optimization_research/reports/khu_research_progress_external_data.md)를 따른다. 기관 간 역할은 사용자가 제공한 잠정 합의를 기준으로 하며, 이후 회의에서 확정되었다고 가정하지 않는다.

후속 진행(2026-09-28): 사용자의 데이터 진단·우선 과제 실행 요청에 따라 원본 실행 DB 7개를 진단하고 첫 성능 예측 실험을 완료했다. 아래 본문은 최초 계획이며, 현재 결과는 [데이터 진단 및 첫 연구 결과](../poc/optimization_research/reports/diagnosis_and_first_experiment.md)를 기준으로 한다. 9,591건 중 9,445건이 첫 회귀 학습 후보이며, local-exhaustive 4,856건으로 그룹 분할 학습·평가를 수행했다. 개발 코드는 `poc/optimization_research/`에 있다.

연구 후속 실험: [GPU 형상 선택 연구 보고서](../poc/optimization_research/reports/gpu_policy_research.md)에서 기존 실측의 8개 GPU 후보를 이용해 SLO 선택 정책과 후보 집합별 오차 보정을 비교했다. 보정 전 정책이 원본 평가에서 54개 선택·관측 위반 0건, 그룹별 90% 보정 정책이 37개 선택·관측 위반 0건이었다. 지금 단계에서는 보정법의 추가 효과가 확인되지 않았다. 다음 단계는 반복 측정과 실제 사용자 SLO 확보이다.

## 1. 목표와 우선순위

경희대 연구 목표는 응용 컨텍스트와 운영 결과를 학습해 자원 요구량, 인프라 형상, 배포·스케일링 판단을 개선하는 것이다. 출력은 플랫폼 중립적인 Deployment Spec이며, 이노그리드는 응용 실행, ETRI는 실제 CSP·리전·VM 선정 및 자원 제어를 담당한다.

첫 연구는 **응용·부하·자원 형상에 따른 성능 예측을 학습하고, SLO를 만족하는 형상을 선택하는 것**으로 한다. 같은 예측 모델을 배포와 스케일링 판단에 재사용한다. 자연어 요구 분석 학습은 검수된 요청–정답 Profile 쌍이 있을 때 별도로 진행한다.

기존 프레임워크의 신규 기능 개발은 우선순위에서 제외한다. 현재 PoC와 Mock Adapter는 실험 연동 대상으로 사용한다. VM당 하나의 주요 응용을 가정한다. 운영 Registry, VM 생성·삭제, 다중 응용 스케줄러, K8s·컨테이너 배포는 개발하지 않는다.

## 2. 확인한 기존 자산

| 자산 | 확인된 역할 | 연구에서 활용할 부분 |
| --- | --- | --- |
| `llmop/` | 자연어·App Spec → ApplicationProfile, LLM 비교 | 요구 분석 기준선과 기존 입력·출력 계약 |
| `resource_ops/` | 자원 정규화, 필수 조건 필터, 점수 추천 | 호환성 필터와 기존 가중 점수 기준선 |
| `deployment_agent/` | Profile·추천 → 판단·Guard·DesiredDeploymentSpec, 운영 판단 | 정책 연동 및 판단 결과 검증 |
| `deployment_agent/go/service-control-api/internal/api/shadow_policy_api.go` | 외부 정책 제안을 기록하는 shadow 클라이언트 | 학습 모델과 기존 규칙의 비교 진입점 |
| `/home/researcher/aiops/khu_policy_agent/` | SQLite ETL, Random Forest 보상 예측, 정책 API | 기존 학습 기준선과 데이터 변환의 재사용 후보 |

`KHU_POLICY_MODE=shadow`, `KHU_POLICY_URL`, `/api/v1/khu-policy/evaluate`가 코드에 존재한다. 2026-09-28부터 현재 Flow에 관측 가용량·자원 건강 상태·검증된 시간당 가격의 출처가 없으면 shadow 평가는 `ABSTAIN`을 기록하고 정책 서버를 호출하지 않는다. **현재 모든 Flow 생성 경로가 이 원본 필드를 보존하지 않으므로 shadow 정책 서버 호출은 중단된 상태다.** 재개하려면 원본 계측·가격·시각·출처를 보존하는 입력 계약과 학습 라벨의 지원 범위 검증이 필요하다. 기본 Go 배포 판단은 그대로 진행된다. 실제 정책 성능이나 배포 효과를 검증했다는 뜻은 아니다.

학습 전 해결해야 할 데이터 의미 문제:

- 종전 shadow payload는 `1 - candidate.Scores.CostEfficiency`를 `cost_per_hour`로, 요청 형상의 용량을 `cpu_available_cores`·`memory_available_mib`·`gpu_available_mib`로, 후보 `Feasible`을 자원 `healthy`로 보냈다. 이 대입은 제거하고 현재 요청은 입력 출처 부족으로 보류한다. 향후에는 관측 시각·출처·단위가 붙은 실제 가용량과 가격을 따로 전달해야 한다.
- `llmop/README.md`는 기본 LLM 벤치마크 설정값을 합성 baseline으로 설명한다. 실측 학습 라벨로 취급하지 않는다.
- README의 legacy 역할 설명과 일부 하위 지침 사이에는 차이가 있다. 사용자 요청과 현재 정식 3개 모듈 경계를 기준으로 설계한다.

## 3. 0단계: SQLite 학습 가능성 진단

### 3.1 이번에 확인한 데이터

원본 실행 DB는 `/home/researcher/aiops/runs/` 아래에 있다. 다음 수치는 2026-09-28 읽기 전용 조회 결과이며, DB 간 중복을 제거한 수치가 아니다. 성공 여부도 수집기의 success 플래그다.

| 디렉터리 / 파일은 모두 `executions.sqlite3` | 실행 수 | 성공 수 | model_id 종류 | replica 값 |
| --- | ---: | ---: | ---: | --- |
| `khu-hf-local-exhaustive` | 4,856 | 4,856 | 9 | 1, 2 |
| `khu-hf-live` | 2,640 | 2,640 | 8 | 1, 2 |
| `khu-hf-remote-216` | 1,085 | 1,075 | 3 | 1, 2, 4 |
| `khu-hf-local-space-filling` | 384 | 384 | 9 | 1, 2 |
| `khu-hf-cpu-scaling-local` | 240 | 240 | 1 | 1, 2 |
| `khu-hf-diverse-cpu-matrix` | 210 | 210 | 8 | 1, 2 |
| `khu-hf-local-novel-matrix` | 176 | 176 | 9 | 1, 2 |

확인된 주요 테이블은 `collection_runs`, `execution_measurements`, `node_samples`이다. 실행 테이블에는 model/revision, device, batch_size, max_length, request_concurrency, cpu_threads, cpu_core_limit, replica_count, latency_p95_ms, examples_per_second, max_rss_kib, GPU 메모리 및 result_json이 있다. 예측 입력·타깃 구성이 가능하다.

파생 DB `khu-live-placement/observed-lifecycle-cpu-scaling-local-20260911.sqlite3`에는 180개 scenario와 560개 action outcome이 있다. action별 건수는 DEPLOY 240, KEEP 160, SCALE_OUT 80, SCALE_IN 80이다. 메타데이터가 `paired_static_measurements_not_executed_lifecycle_transitions`임을 명시하며 UPDATE는 미지원으로 기록돼 있다. 여기의 DEPLOY 역시 모델 벤치마크 배치 근거이지 실제 클라우드 배포 성공 기록은 아니다.

`khu-deployment-scenarios/scenarios.incomplete-seed42.sqlite3` 및 `scenarios.pre-feasibility-fix-seed42.sqlite3`에는 각각 5,000개 scenario/40,000개 outcome이 있다. 생성 코드와 README상 합성 시나리오 계열이며 파일명에도 미완료/수정 전 상태가 표시돼 있다. 검증 없이 주 학습 데이터에 합치지 않는다.

### 3.2 데이터 해석에 따른 구체적 결정

- **첫 타깃:** 원본 실측의 latency, throughput, 피크 메모리 예측. 첫 응용군은 데이터가 있는 인코더 추론과 causal-LM forward 벤치마크로 제한한다. 전체 생성 서비스 성능으로 확대 해석하지 않는다.
- **기존 기준선:** `khu_policy_agent/learning.py`의 200-tree RandomForestRegressor 보상 예측을 비교 대상으로 보존한다. 기존 구현은 source_scenario_id 단위 분할을 사용한다. 새 실험은 실행·응용·호스트 중복과 시간 누수도 확인한다.
- **추가 모델:** 성능을 직접 예측하는 Random Forest/CatBoost와 불확실성 기반 선택을 비교한다. 기존 가중 보상 재현과 실제 SLO 개선을 구분한다.
- **분할 후보:** local-exhaustive를 주 학습 후보, novel-matrix/space-filling을 조건 일반화 후보, remote-216을 호스트 이동 평가 후보로 검토한다. 이름만으로 독립 test라 판단하지 않고 workload·revision·실행 중복을 검사한 뒤 확정한다.
- **실패 학습 제한:** 열람한 원본 중 remote-216에 실패 10건이 있다. 원인 분류 전에는 OOM/배포 실패로 단정하지 않는다. 기존 ETL은 success=1 및 complete run만 읽으므로 별도 실패 데이터 추출이 필요하다. 현재 건수만으로 안정적인 실패 분류기를 기대하지 않는다.
- **요구 분석 라벨:** 기존 `_application_for`는 모델 파라미터 크기와 규칙으로 요구량·SLO를 만든다. 검수된 자연어–정답 Profile 데이터로 사용할 수 없다. 특히 causal workload의 GPU 필수 설정은 생성 규칙이며 사용자 요구와 구분해야 한다.
- **비용·자원 의미:** 기존 ETL의 비용은 proxy이고 storage는 ETL 실행 시 호스트 디스크를 조회한다. 역사적 CSP 가격/당시 자원으로 취급하지 않는다. GPU replica는 같은 호스트에서 측정한 구성이라 여러 VM의 스케일링과 같지 않다.
- **입력 시점:** ETL은 before snapshot을 우선 읽지만 없으면 다른 phase를 선택하고 GPU running sample도 읽는다. 학습 입력의 실행 전/후 가용성을 필드별로 확인하고 이후 관측 유래 특성은 배포 전 모델에서 제거한다.
- **부하 의미:** 측정은 closed-loop concurrency 및 batch 단위 지연을 포함한다. examples/sec, requests/sec, batch p95, 사용자 요청 p95를 구분한다. 실제 유입률 변화·대기열·증설 지연 연구에는 추가 부하 실험이 필요하다.

### 3.3 이어서 수행할 전체 진단

확인한 DB를 읽기 전용 연결 및 일관된 스냅샷으로 조사한다. 쓰기 중인 WAL DB는 본체 파일만 복사하지 않고 SQLite backup 등으로 일관성을 확보한다. 원본을 변경하지 않는다.

조사 항목:

1. 테이블·컬럼·키·단위·기간·행 수·수집 주기·스키마 버전.
2. 응용, 실행, 형상, 모니터링, 판단, 실행 결과 간 연결 키와 실제 join 성공률.
3. 결측·중복·이상치, 응용 종류, 형상 종류, 부하 범위, 성공·실패 분포.
4. 실측·Mock·합성·LLM 생성값·사용자 검수값의 출처 구분.
5. 판단 시점 이전에 알 수 있었던 입력과 판단 이후 성과의 시간 구분.
6. 같은 응용·비슷한 부하에서 서로 다른 자원 형상을 실행한 사례의 유무.
7. 스케일링 전후의 상태, 실제 실행 여부, 준비 지연, 관측 기간의 존재.

아래 기준으로 추가 데이터가 필요한 과제를 구분한다. 실제 발견한 스키마는 3.1절에 기록했다.

| DB에 있는 데이터 | 가능한 첫 과제 | 추가로 필요한 데이터 |
| --- | --- | --- |
| CPU/GPU/메모리 사용률만 존재 | 자원 사용량·부하 시계열 예측 | 응용 식별자, 형상, 부하, SLO 성과 |
| 응용 + 실행 형상 + latency/throughput/OOM | 성능·실패 예측과 형상 추천 | 다양한 형상·부하에서 반복 실행 |
| 자연어 요청 + 검수된 ApplicationProfile | 요구 분석 지도학습 | 어려운 사례와 독립 검수 평가셋 |
| 운영 상태 + 실행 action + 이후 성과 | action 조건부 결과 예측 | 행동 다양성, 시간 연결, 정책 정보 |
| 상태 전이 + 보상 + 행동 선택 정보 | 후속 bandit/RL 연구 검토 | 행동 지원 범위와 평가 조건 검증 |

**자원 사용률만으로 최소 필요 CPU 수나 최적 GPU 수를 정답으로 만들지 않는다.** 과거 배포 형상은 당시 선택된 값이며, 최소 비용으로 SLO를 만족하는 정답이라는 보장이 없다. 사용률은 자원 제한이나 병목의 영향도 받는다.

산출물: `data_audit.md`, 데이터 사전, 학습 가능 과제 목록, 데이터 부족 목록, 원본/스냅샷 식별값.

## 4. 네 연구 항목의 학습 설계

### 4.1 요구사항 분석

요구사항을 두 부분으로 나눈다.

- 명시된 요구 추출: 요청·응용 메타데이터 → task type, SLO, 예산, 장치 제약, 이미지·배치 조건. 기존 LLM/규칙을 기준선으로 사용한다. 학습은 검수된 요청–Profile 쌍으로 수행한다.
- 미지의 요구량 추정: 모델 크기·입력 크기·동시 요청·부하·실행 설정 → 메모리/VRAM 피크 및 가능한 자원 형상. 실측 성능 모델을 사용한다.

명시적 제약, 관측값, 추정값, 누락값을 구별하고 출처·단위·신뢰 정보를 보존한다. 정보가 부족하면 누락을 표시한다. LLM이 생성한 Profile은 자동 정답으로 채택하지 않는다.

평가: 필드별 정확도/F1, 단위 변환 오류, 필수 제약 누락률, JSON 검증 통과율, 추정량 오차와 과소 추정률. 자연어 추출 정확도와 자원 추정 정확도를 별도로 보고한다.

### 4.2 인프라 추천: 첫 번째 주력 실험

학습 입력은 `(응용 특성, 유입 부하, 자원 형상, 실행 설정)`이다. 타깃은 가용한 실측값에 따라 p95 latency, throughput, 피크 메모리/VRAM, OOM·배포 실패 여부로 선정한다. 요청 수준 샘플이 없다면 평균값에서 p95를 임의로 생성하지 않는다.

기준선은 응용별 통계/단순 회귀, 기존 가중 점수 추천, 기존 Random Forest 보상 모델이다. 추가 학습 후보는 범주형·수치형 특성이 섞인 표 데이터용 CatBoost 회귀로 한다. 실패 분류는 충분한 실패 사례를 확보한 뒤 진행한다. 작은 MLP는 표본 규모와 형상 다양성이 확보된 뒤 비교한다. 알고리즘의 우열은 고정 평가셋 결과로 결정한다.

추천 절차:

1. 명시적 최소 조건·가속기 호환성·이미지/아키텍처·배치 조건으로 후보 필터링.
2. 필터를 통과한 형상별 성능과 예측 불확실성 계산.
3. latency 상한, throughput 하한, 메모리 여유 등 보수적인 조건으로 SLO 충족 여부 평가.
4. 충족 후보 중 비용 또는 자원 사용량이 작은 형상 선택.
5. 학습 범위 밖 형상·응용이나 유효 후보 부재는 판단 보류 사유로 반환.

비용은 통화·시간 단위·견적 시점이 확인된 데이터만 사용한다. 실제 가격이 없다면 CPU·GPU 시간 등 자원량 대리 지표로 평가하고 금전적 절감으로 보고하지 않는다. CSP·리전·실제 VM의 최종 선택은 ETRI 경계에 남긴다.

예측 구간은 분위수 회귀와 별도 calibration 집합을 사용해 검토한다. 시간 변화·새 응용에서는 구간의 실제 포함률을 따로 측정하며, 일반적인 calibration 성질이 자동 보장된다고 가정하지 않는다.

### 4.3 배포·스케일링 판단

판단 입력: ApplicationProfile, 후보 형상/예측 성능, 현재 배포 상태, 최근 모니터링 창, 이전 action과 실행 상태.

| 판단 | 조건과 연구 대상 |
| --- | --- |
| `DEPLOY` | 현재 배포가 없고 적합한 형상이 있을 때 |
| `UPDATE` | 버전·설정·요구 명세의 실제 변경이 있고 갱신 조건을 만족할 때 |
| `SCALE_OUT` | 예상 부하에서 SLO 충족에 추가 replica가 필요할 때 |
| `SCALE_IN` | replica 감소 이후에도 보수적으로 SLO를 만족할 때 |
| `KEEP` | 현재 구성이 적합하고 변경 이득이 충분하지 않을 때 |

`ABSTAIN`은 모델의 판단 보류 상태로 별도 표현한다. 기존 `REJECT`/`RETRY`와의 매핑은 계약에서 정의한다. 불확실한 예측을 정상 `KEEP`와 혼동하지 않는다.

첫 정책은 최근 부하 예측 + 후보 replica 수별 성능 예측 + 제한된 후보 탐색으로 구현한다. min/max replica, cooldown, 최소 유지 시간, 동시 변경 방지 및 준비 지연을 반영한다. UPDATE를 단순 고부하의 대체 action으로 사용하지 않는다.

초기 후보 수는 작게 제한하고 시뮬레이션으로 검증한다. replica 증가에 처리량이 선형 증가한다고 가정하지 않는다. 응용의 복제 가능성, 공유 병목, 상태 보존·drain 가능 여부를 확인한다. VM 수와 replica 수의 대응도 명시한다.

동일 상태에서 선택하지 않은 action의 성과는 로그에 없다. 따라서 기존 모니터링 trace를 다른 replica 정책에 그대로 적용한 결과를 실제 정책 효과로 주장하지 않는다. 제어된 반복 실행 또는 보정·검증된 시뮬레이터에서 비교한다.

### 4.4 배포·제어에 특화된 추론 최적화

두 가지 개선을 구분해 측정한다.

1. **판단 품질:** 새 배포 결과로 성능·실패 예측을 재학습하고 불확실성 보정, 후보 추천과 변경 판단의 품질을 높인다.
2. **판단 계산 비용:** 검증된 복잡한 정책/LLM의 출력을 경량 모델로 증류하거나, 작은 모델과 큰 모델의 선택기를 학습해 판단 지연·호출 비용을 낮춘다.

증류에는 검수 또는 실행 성과로 품질이 확인된 사례를 사용한다. 평가셋은 학습·증류·튜닝에서 제외한다. 판단 모델의 추론 지연과 배포된 응용의 서비스 latency는 별도 지표로 기록한다.

bandit은 컨텍스트별 형상 선택의 후속 후보이다. 장기적인 변경 비용과 상태 전이가 중요하고 해당 로그가 충분할 때 offline RL을 검토한다. IPS/DR 정책 평가는 기록 정책의 행동 확률과 지원 범위 등 가정을 확인한 경우에 적용한다. 확률이 없거나 선택되지 않은 행동의 지원이 없으면 객관적인 정책 개선 근거로 사용하지 않는다.

## 5. 데이터셋과 검증 원칙

학습 표본을 세 종류로 정의한다.

- 요구 분석: `(요청, 응용 메타데이터, 검수 Profile, 검수 출처)`.
- 성능 예측: `(run_id, app/version, load window, resource shape, observed outcome)`.
- 제어: `(decision_id, state_before, eligible_actions, action, execution_result, state_after, outcome_window)`.

원본 SQLite는 수집 저장소로 유지하고, 학습용 스냅샷은 버전이 있는 Parquet/JSONL로 내보낸다. 결측과 0을 구별하고 각 특성의 값·단위·출처·관측 시각을 보존한다. 전처리 설정, 분할 ID, 데이터 해시, 코드 버전, 모델 설정, seed를 실험마다 저장한다.

분할 및 누수 방지:

- 기존 응용의 미래 성능은 시간 순서 분할로 평가한다.
- 새 응용·새 형상 일반화는 별도의 그룹 홀드아웃으로 평가한다.
- 같은 실행과 겹치는 관측 창을 여러 분할에 넣지 않는다. 입력/성과 창 길이에 따른 시간 간격을 둔다.
- 정규화·대치·범주 처리·특성 선택은 train에서 학습하고 calibration/validation/test의 역할을 분리한다.
- CPU·메모리의 실행 후 측정값을 배포 전 입력으로 사용하지 않는다. 운영 판단에서는 판단 시점까지의 관측만 사용한다.
- Mock·합성 평가는 실측 평가와 별도로 보고한다. 같은 생성 규칙을 학습·시험에 사용한 결과만으로 일반화 성능을 주장하지 않는다.

## 6. 실험 계획과 완료 기준

| 실험 | 비교 | 주 지표 |
| --- | --- | --- |
| E0 데이터 진단 | 테이블·join·라벨 출처 검사 | 유효 실행 수, 다양성, 결측률, 실측 비율 |
| E1 요구 분석 | 기존 규칙/LLM, 학습 모델 | 필드 정확도, 누락률, 제약 위반률 |
| E2 성능 예측 | 응용별 통계, 단순 회귀, CatBoost | MAE, 분위수 손실, 실패 분류 지표, 구간 포함률 |
| E3 형상 추천 | 기존 점수, 최소 적합 형상, 학습 추천 | SLO 충족률, 실패/OOM률, 비용 또는 자원량 |
| E4 스케일링 | 고정 replica, 임계치 정책, 예측 정책 | SLO 위반 시간, 자원 시간, 변경 횟수, 준비 지연 영향 |
| E5 추론 최적화 | 원 정책, 경량 모델/선택기 | 판단 품질 유지, 판단 p50/p95 지연, 호출 비용 |

oracle 대비 regret는 동일 조건에서 후보별 성과를 실제 측정했거나 평가 환경에서 알고 있는 경우에만 계산한다. 학습 모델이 스스로 생성한 예측값을 정답으로 사용해 추천 성능을 검증하지 않는다.

ablation: 응용 컨텍스트 제거, 불확실성 제거, 운영 이력 제거를 비교해 각 요소의 기여를 확인한다. 결과는 실행/응용 단위 반복과 불확실성 구간을 함께 제시한다.

완료 기준: 고정 평가셋에서 기준선 대비 SLO·실패율이 허용 범위 내에 있으면서 비용/자원량 또는 판단 비용을 개선해야 한다. 구체적 허용치와 최소 개선 폭은 E0 이후, test 결과를 보기 전에 고정한다. 개선이 없으면 그 결과와 원인을 보고하고 기존 기준선을 유지한다.

## 7. 최초 계획의 개발 구조와 기관 연동

아래는 최초 제안한 디렉터리 구조다. 현재는 `poc/optimization_research/`가 생성되어 실제 실험 스크립트·테스트·보고서를 담고 있다.

```text
ai-mcmp/
  llmop/                      # 기존 Go 요구 분석
  resource_ops/               # 기존 Go 자원 필터·추천
  deployment_agent/           # 기존 Go 판단 계약·Guard
  poc/optimization_research/                   # 신규 Python 학습·실험 영역
    data/                     # SQLite 추출, 검증, 분할
    features/                 # 응용·형상·시계열 특성
    models/                   # 요구량·성능·실패·부하 모델
    policies/                 # 추천·배포·스케일링 판단
    evaluation/               # 오프라인 평가·시뮬레이션
    serving/                  # 기존 shadow API와 연동
    configs/
    tests/
  docs/research-development-plan.md
```

최초 계획은 학습·실험을 Python으로 분리하고 기존 Go 핵심 서비스를 유지하는 것이었다. 현재 오프라인 연구는 `poc/optimization_research/`에서 진행했으며, 기존 shadow 클라이언트는 실제 가용량·가격의 출처가 보존될 때까지 제안을 보류한다. shadow 비교 자체도 제안 action 실행 후의 실제 효과 검증은 아니다.

권장 정책 출력에는 action, 요구 형상, 목표 replica 수, 예상 성과·불확실성, 판단 사유, 보류 사유, 모델·데이터 버전을 포함한다. 임시 candidate ID는 입력 후보와의 연결 키로만 사용하고, 플랫폼 중립적 Spec의 의미를 실제 VM ID에 종속시키지 않는다.

기관 협의 항목: 데이터 제공 규격/주기, 형상 vs VM 후보 추천 경계, 응용 복제 가능성, 비용/가격 의미, UPDATE 조건, 판단 API 배치 위치, 피드백 연결 키와 실행 완료 이벤트. 확정 전에는 versioned adapter에서 차이를 흡수한다.

## 8. 최초 단계별 진행안

기간은 DB 접근·데이터 품질·컴퓨팅 자원에 따라 조정하는 작업 규모 초안이다.

| 단계 | 예상 기간 | 산출물·다음 단계 진입 조건 |
| --- | --- | --- |
| P0 DB 진단 | 2–3 작업일 | 데이터 사전, 학습 가능성 판단. 성과 라벨 부족 시 수집 설계 우선 |
| P1 데이터셋·기준선 | 약 1주 | 재현 가능한 추출/분할, 기존 정책 및 통계 기준선 |
| P2 성능 예측·추천 | 약 1–2주 | 모델 아티팩트, E2/E3, 미지원 입력 처리 |
| P3 배포·스케일링 정책 | 약 1–2주 | action 계약, 제한 조건, E4 시뮬레이션 |
| P4 shadow·보고서 | 약 1주 | 기존 PoC 연결, 비교 로그, 설계/API/알고리즘/검증 문서 |
| 후속 연구 | 데이터 조건 충족 후 | 요구 분석 미세조정, 증류/선택기, bandit 또는 offline RL |

실측 데이터가 부족하면 위 일정에 별도 실험 수집 기간이 추가된다. 첫 실행 목표는 **실측 근거가 있는 한 종류의 응용군에서 성능 예측과 형상 추천을 재현하고 기존 정책과 비교하는 것**이다. 특정 응용군은 DB 분포를 본 뒤 선정한다.

## 9. 최초 다음 작업 기록

최초 계획 당시에는 학습·서비스 실행·기존 코드 변경을 수행하지 않았다. 이후 KHU 실행 DB 7개를 진단하고 실측 특성·타깃·분할 기준을 확정해 첫 성능 예측을 실행했다. 그 뒤의 독립 공개 데이터 평가와 남은 검증 조건은 [현재 연구 결과](../poc/optimization_research/reports/khu_research_progress_external_data.md)에 기록했다.

## 참고 근거

기존 구조 판단: `llmop/README.md`, `resource_ops/README.md`, `resource_ops/docs/formulas.md`, `deployment_agent/README.md`, `deployment_agent/go/service-control-api/internal/api/shadow_policy_api.go`, `/home/researcher/aiops/khu_policy_agent/{README.md,learning.py,live_dataset.py}`. 데이터 수치는 위 SQLite의 읽기 전용 조회 결과다. 서비스 실행 검증은 포함하지 않았다.

- [CatBoost 범주형 특성 처리](https://catboost.ai/docs/en/concepts/algorithm-main-stages_cat-to-numberic): 표 데이터 학습 후보의 기술적 근거. 이 데이터에서의 성능 우위를 의미하지 않는다.
- [MAPIE 회귀 예측 구간](https://mapie.readthedocs.io/en/stable/api/regression/): 불확실성 추정 구현 후보.
- [Vowpal Wabbit 오프라인 정책 평가](https://vowpalwabbit.org/docs/vowpal_wabbit/python/latest/tutorials/off_policy_evaluation.html): 로그의 미관측 행동 성과와 정책 평가 가정 검토 근거.
