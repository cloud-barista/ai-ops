# 기존 SQLite의 미사용 판단 신호 감사 (2026-09-28)

## 결론

`khu-hf-*` 실행 DB 7개 외에 KHU 배치/수명주기 DB가 있다. 그러나 이 DB의 `SCALE_OUT`·`SCALE_IN` 결과는 **실제 확장 동작의 전후 기록이 아니라**, 같은 작업을 GPU 복제 수 1/2/4개에서 따로 실행한 **정적 측정의 재표현**이다. `UPDATE` 실측은 없다. `oracle_action`과 보상은 이 측정값에 사후 계산식과 임의 가격을 적용한 결과이므로, 독립적인 운영자 행동 정답으로 사용하면 안 된다. 배포 전 특징만 새로 만들면 *정적 복제 수 변경 시 정상 상태 성능*은 평가할 수 있다. 이는 기존 실행 DB에 있는 물리 측정과 같은 예측 대상이며, 실제 오토스케일러 전이 지연·위험이나 새 사용자 SLO에 대한 정답은 아니다.

두 가지 검증 오류도 확인했다. 첫째, action DB의 후보 특징 `candidate_gpu_utilization`은 후보 실행이 끝나야 알 수 있는 `running` 계측값이다. 둘째, `throughput_rps`는 실제 요청/초가 아니라 원본 **examples/초**를 그대로 복사한다. 배치 크기가 2 이상이면 SLO 충족 라벨을 과대평가한다. 이 테이블의 기존 학습·테스트 점수는 수정 전에는 운영 배포 효과의 근거가 될 수 없다.

## 원본과 파생 DB의 구분

- `/path/to/aiops/runs/khu-hf-*/executions.sqlite3` 7개는 실측 원본이다. 테이블은 `collection_runs`, `execution_measurements`, `node_samples`뿐이다. 성공 9,581건/실패 10건, 호스트 `icns`와 `ubuntu216`이지만 성공한 동일 모델·동일 자원 조건의 호스트 간 짝은 0건이다. 공통 Qwen 모델의 원격 5건은 모두 실패했다. 원본에는 앱 요청→요구 형상 라벨, VM 생성·삭제, 업데이트, scale 명령 시점과 완료 시점, CSP 실가격이 없다.
- `/path/to/aiops/runs/khu-live-placement/*.sqlite3` 및 원격의 `placement.sqlite3`, `observed-lifecycle.sqlite3`는 `deployment_scenarios`와 `action_outcomes`를 갖는 **ETL 산출물**이다. 같은 `execution_id`가 `DEPLOY`, `KEEP`, 확장/축소 결과로 반복된다. 여러 ETL DB를 독립 표본처럼 합치면 물리 실행을 중복 집계한다.
- `/path/to/aiops/runs/khu-deployment-scenarios/scenarios.incomplete-seed42.sqlite3`와 `scenarios.pre-feasibility-fix-seed42.sqlite3`에는 각각 5,000 시나리오/40,000 결과가 있지만 설정에 `generator_version: khu-deployment-scenario-generator/v1` 및 FCA 시나리오를 `source_db`로 기록한다. 별도 KHU 실측이 아니다. 다른 FCA 실행 DB와 `.fca/jobs.db`도 발견했으나, 각각 별도 분산작업 자료와 단일 작업 기록이어서 여기의 AI-App VM 수명주기 전이 정답으로 합치지 않았다.

ETL 자체가 `transition_evidence: paired_static_measurements_not_executed_lifecycle_transitions`, `observed_actions: [DEPLOY, KEEP]`, `paired_static_measurement_actions: [SCALE_OUT, SCALE_IN]`, `unsupported_without_new_measurements: [UPDATE]`를 기록한다. 구현도 기준 형상과 대상 형상의 **서로 다른 `execution_measurements` 행**을 연결한다: live_dataset.py (`/path/to/aiops/khu_policy_agent/live_dataset.py:161`), 정적 전이 구성 (`/path/to/aiops/khu_policy_agent/live_dataset.py:242`). `lifecycle_state`는 기준 실행의 지연·처리량·자원 사용률에서 규칙으로 만든다. 앱 최소 RAM/VRAM은 모델 파라미터 크기 공식에서, 비용은 CPU `0.45`, GPU당 `4.80` USD/h 상수에서 만든 **실험 프록시**다: 요구사항/비용 생성 (`/path/to/aiops/khu_policy_agent/live_dataset.py:617`), 가격 상수 (`/path/to/aiops/khu_policy_agent/live_dataset.py:29`).

## 두 lifecycle DB의 독립 재검산

| 원본→파생 DB | 시나리오/결과 | 고유 원본 실행 | 사후 GPU 사용률 특징=대상 실행 계측 | 원래 SLO 충족 | 배치 크기로 요청/초 보정 후 충족 | 잘못된 충족 라벨 |
|---|---:|---:|---:|---:|---:|---:|
| local-space-filling→observed-lifecycle-local-space-filling | 428 / 1,120 | 384 | 1,056 / 1,056 GPU 결과 | 972 | 627 | 345 |
| remote-216→remote-216-canonical-lifecycle | 277 / 1,096 | 344 | 1,040 / 1,040 GPU 결과 | 972 | 861 | 111 |

보정은 `request_rps = execution_measurements.examples_per_second / batch_size`만 바꾼 것이다. 지연 조건과 파생 DB의 feasibility 조건은 그대로 두었다. 따라서 위 345·111건은 **처리량 단위만 바로잡아도** 원래의 `slo_satisfied=1`이 실패로 바뀌는 건수다. 동일 원본 실행이 여러 action 행에 나타나므로 이 수는 독립 관측 수가 아니다. `latency_p95_ms`도 실제 서비스 요청이 아니라 벤치마크 배치 p95다. 수집기의 목표 처리량·지연은 사용자가 정한 값이 아니라 코드 프로필/공식에서 생성했다: 수집기 예시 (`/path/to/aiops/khu_policy_agent/live_collector.py:708`), 결과 매핑 (`/path/to/aiops/khu_policy_agent/live_dataset.py:708`).

`candidate_gpu_utilization`은 `_candidate_for()` (`/path/to/aiops/khu_policy_agent/live_dataset.py:791`)가 대상 `Measurement`의 `running` GPU 샘플 평균 (`/path/to/aiops/khu_policy_agent/live_dataset.py:827`)을 읽어 만든다. 같은 행의 `outcome_json.measured.gpu_utilization_percent`도 그 함수에서 온다. 훈련 코드의 `load_rows()` (`/path/to/aiops/khu_policy_agent/learning.py:132`)는 `feature_json` 전체를 특징으로 사용한다. 따라서 대상 실행 전에는 모르는 수치가 모델 입력에 들어간다. 기존 저장된 RF manifest는 직접 `DEPLOY` 배치 자료만 학습했다고 표시하며, 별도 lifecycle 학습 모델은 찾지 못했다. 예를 들어 `artifact-v2` manifest (`/path/to/aiops/runs/khu-live-placement/artifact-v2/khu-policy.manifest.json`)의 테스트 `action_macro_f1=1.0`은 유일한 action이 `DEPLOY`인 설정이고, 위 누출·단위 오류를 고친 성능이 아니다. 점수가 얼마나 부풀었는지는 재학습·재평가 전에는 단정할 수 없다.

## 다른 미사용 필드

`node_samples.gpus_json`에는 매 샘플 `power_watts`가 있다. 이는 `nvidia-smi power.draw`의 보드 전력이다. 그러나 주 exhaustive 원본의 GPU 1개 실행 2,300건 중 1,547건, GPU 2개 실행 2,300건 중 2,190건은 *실행 중 평균 선택 GPU 전력 − 실행 직전 전력*이 0 이하이다. 중앙 실행 시간은 각각 약 0.60초/1.96초, `running` 샘플은 약 4개/28개다. CPU-only 실행 256건 중 92건은 시작 시 두 GPU 전력이 합계 50W를 넘고, 그중 73건은 동시에 GPU 이용률 0%다. 바로 앞 실행의 전력 상태·센서 지연·모델 로딩과 추론 구간 혼합 때문에 현재 기록에서 **증분 GPU 에너지 정답**을 안정적으로 산출하기 어렵다. CPU/시스템 전력도 없다. 샘플러는 전체 모델 로드·추론 동안 0.1초 간격으로 `nvidia-smi`를 호출하며 추론 구간의 별도 시각을 저장하지 않는다: 샘플러 (`/path/to/aiops/khu_policy_agent/live_collector.py:1283`), 전력 조회 (`/path/to/aiops/khu_policy_agent/live_collector.py:2007`).

`load_seconds`는 성공 실행 모두에 있지만 로컬 캐시에서 모델/토크나이저를 읽는 시간이다. GPU 전송, 프로세스 생성, VM 생성/배포 완료를 포함하지 않는다. 모델 적재 시간의 보조 예측 대상은 될 수 있어도 배포 준비 시간이나 scale 전이 시간의 정답으로 이름 붙여서는 안 된다. 분류 모델과 causal LM 모두 로딩 계측 뒤에 `model.to(device)`를 수행한다: 분류 (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py:279`), causal LM (`/path/to/aiops/khu_policy_agent/live_collector.py:1752`).

## 재현 및 연구 경계

아래 명령은 DB를 읽기 전용으로 열고 위 두 lifecycle DB의 핵심 수치를 다시 계산한다. `ROOT`는 원본 실행 DB 위치다.

```python
import json, sqlite3
from pathlib import Path

root = Path('/path/to/aiops/runs')
pairs = [
    (root/'khu-live-placement/observed-lifecycle-local-space-filling.sqlite3',
     root/'khu-hf-local-space-filling/executions.sqlite3'),
    (root/'khu-live-placement/remote-216-canonical-lifecycle.sqlite3',
     root/'khu-hf-remote-216/executions.sqlite3'),
]
def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)
for derived_path, source_path in pairs:
    with ro(derived_path) as derived, ro(source_path) as source:
        batch = dict(source.execute('SELECT execution_id,batch_size FROM execution_measurements'))
        target = {sid: json.loads(app)['requirements']['slo']['throughput_rps_min']
                  for sid, app in derived.execute('SELECT scenario_id,application_json FROM deployment_scenarios')}
        n = yes = corrected = gpu = leaked = 0
        for sid, old, eps, fj, oj in derived.execute(
            'SELECT scenario_id,slo_satisfied,throughput_rps,feature_json,outcome_json FROM action_outcomes'):
            features, outcome = json.loads(fj), json.loads(oj)
            n += 1; yes += bool(old)
            corrected += bool(old) and eps / batch[outcome['execution_id']] >= target[sid]
            if features['candidate_gpu_count'] > 0:
                gpu += 1
                leaked += abs(features['candidate_gpu_utilization'] -
                              outcome['measured']['gpu_utilization_percent']) < 1e-9
        print(derived_path.name, n, yes, corrected, gpu, leaked)
```

후속 정적 평가를 한다면 원본 `execution_measurements`를 고유 실행으로 취급하고 모델·입력 조건·수집 라운드를 함께 묶어 분할해야 한다. 후보 입력에는 배포 전에 알 수 있는 모델/작업 특성, 자원 형상, **결정 시점 이전**의 노드 상태만 허용한다. 대상 실행의 `running`/`after` GPU 사용률, 대상 실행 직전 스냅샷을 모든 후보에 동시에 알 수 있다는 가정, `oracle_action`, 사후 보상은 입력에서 제외한다. 처리량 단위를 요청/초로 고친 뒤 기준선과 비교할 수는 있지만, 그 결과로 운영 scale의 시간·실패 위험이나 학술적 신규성을 입증할 수는 없다.
