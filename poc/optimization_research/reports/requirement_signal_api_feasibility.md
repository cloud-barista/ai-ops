# 기존 학습 모델의 오프라인 응용 요구 신호 재사용 감사

작성일: 2026-09-28. 기존 SQLite·저장 모델·추론 코드를 읽고 해시와 로컬 추론 호환성을 확인했다. 이 감사에서 모델을 재학습하거나 배포 출력을 만들지 않았다.

## 판정

**제한적 GO:** 관측 GPU 예약 메모리 피크 예측치와 로컬 캐시 모델·토크나이저 적재시간 예측치를 서로 독립적인 **연구용 보조 신호**로 내보낼 수 있다. 각 필드는 `RESEARCH_ESTIMATE` 또는 `ABSTAIN`, 단위·장치 프로필·학습 모델과의 관계·아티팩트 해시를 함께 반환해야 한다. 등록 시 파라미터 바이트 수와 작업 조건이 실행 *전에* 같은 정의로 확보된다는 가정이 필요하다.

**통합 형상 판단은 NO-GO:** [8후보 평가기](../scripts/assess_gpu_shape.py)는 학습에 있는 모델 ID·revision과 같은 호스트의 GPU 1/2 복제·CPU affinity 2/4/8/16 조건만 받는다. 가장 나은 [조건부 VRAM 연구 모델](../scripts/evaluate_vram_conditional.py)은 반대로 저장 모델의 9개 학습 모델 ID를 거부하고 **새 모델**만 예측한다. 따라서 둘의 검증 범위가 겹치는 “새 앱의 8후보 성능+메모리 요구 형상”은 현재 만들 수 없다. 두 연구 신호를 후보의 승인·거부, `DesiredDeploymentSpec`, `DEPLOY`나 `SCALE_OUT`의 근거로 결합하지 않는다.

전역 [VRAM 상한 모델](../scripts/evaluate_vram_upper.py)은 조건부 모델과 다르게 학습 모델 ID를 거부하지 않는다. 알려진 모델에서 8후보 결과 옆에 숫자를 놓는 것은 *기술적으로* 가능하다. 이 숫자는 GPU 1 replica·CPU affinity 2코어의 관측 피크 추정치 **하나**이며 8후보 각각의 메모리 예측값이 아니다. 저장된 최종 모델은 바로 그 모델의 주 캠페인 행으로 학습했다. 보고된 모델 제외 점수는 각 fold에서 **별도 재학습한 모델**의 점수이며, 이 동시 출력의 외부 검증 점수가 아니다. 출력한다면 `training_relation=IN_SAMPLE_EXPLORATORY`, `target=observed_cuda_reserved_peak`로 별도 증거 필드에만 둔다. 조건부 모델의 새 모델 예측과 같은 품질 등급으로 표시하지 않는다.

## 확인한 라벨·저장 아티팩트

고정 SQLite 추출 (`../data/audit_v1/measurements.parquet`)의 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. VRAM 정답은 단일 GPU에서 `torch.cuda.max_memory_reserved()`로 관측한 예약 피크(GiB), 적재시간 정답은 로컬에 캐시된 Hugging Face 모델·토크나이저를 읽은 `load_seconds`다. 주 캠페인은 동일 `icns` 호스트·RTX PRO 5000 Blackwell GPU 0·CUDA 1 replica·CPU affinity 2코어의 성공 실행 **575개/9모델**이다. 추가 조건 18/44개는 같은 모델의 다른 입력이고 주 캠페인보다 먼저 수집됐다. 파라미터 바이트 수와 작업 종류는 기존 자료에서 실행 *후* `result_json`에 기록된다. [라벨·시점 감사](sqlite_requirement_analysis_new_task_feasibility.md), [메모리 감사](memory_feasibility_review.md), [적재시간 실험](load_time_findings.md).

| 연구 신호 | 우선 고려할 저장 모델 SHA-256 | 검증 범위와 실패 |
| --- | --- | --- |
| 작업 종류·크기 조건부 관측 예약 피크 90% 경험적 상한 | `vram_conditional_v1/research_estimator.joblib` (`../artifacts/vram_conditional_v1/research_estimator.joblib`) `21387fd6ba6b6185737ed9e10ba0930b9c2696e033ac532287bd84e8f6501e01` | 새 모델, 동일 물리 프로필, 같은 작업 종류의 서로 다른 donor ≥2모델만 허용한다. encoder 8모델의 모델별 평균 커버리지는 주/새 입력/공간표본 `95.1/93.8/95.0%`; ALBERT 단독 제외는 `62%`, 최대 부족 `320 MiB`. 새 모델 90% 보장은 없다. [평가](vram_conditional_findings.md) |
| 전역 관측 예약 피크 90% 경험적 상한 | `vram_upper_v1/research_estimator.joblib` (`../artifacts/vram_upper_v1/research_estimator.joblib`) `8fb66e867b98d1e747956d33b23fbc823fcf65cb5f9238ccee2763fce32a328e` | 동일 물리 프로필·동종 donor ≥2모델은 검사하나 학습 모델 ID는 검사하지 않는다. 알려진 모델의 추론은 학습 내 예측으로 명시해야 한다. Qwen 모델 제외 커버리지 `27%`; 동종 donor가 없어 운영에서는 보류해야 한다. [평가](vram_upper_findings.md) |
| 지원 범위 표시된 로컬 적재시간 | `load_support_v1/support_aware_size_tree_all_main.joblib` (`../artifacts/load_support_v1/support_aware_size_tree_all_main.joblib`) `39a8d614c101a89d1ec65dca136a4c24415da3b0d41ff8f4209422fd9ee76131` | 같은 작업 종류 donor ≥2모델만 답한다. 모델 제외 encoder 8/9모델의 주 MALE `0.246`, 절대오차 `0.039초`; Qwen 보류. 모델 ID·호스트·GPU·CPU 프로필과 크기 범위는 함수가 강제하지 않아 API 경계에서 검사해야 한다. [평가](load_support_findings.md) |

기존 크기만의 적재시간 모델 `load_time_v1/size_only_extra_trees_all_main.joblib` (`../artifacts/load_time_v1/size_only_extra_trees_all_main.joblib`)의 SHA-256은 `af645e5eb8f66a327e0d1d82519707377c61361cc5e15ef6b7d807587dae5c02`이다. 지원 범위 포함 모델은 이 점 예측기에 작업 종류 donor 수 게이트를 붙인 것으로, 답한 모델의 예측값을 개선한 새 회귀기가 아니다. 네 모델과 소스 파일의 현행 해시는 각 [조건부](../artifacts/vram_conditional_v1/manifest.json)·[전역](../artifacts/vram_upper_v1/manifest.json)·[적재시간](../artifacts/load_time_v1/manifest.json)·[지원 범위](../artifacts/load_support_v1/manifest.json) manifest와 일치한다.

## 오프라인 인터페이스 조건

1. 로컬 고정 아티팩트 경로와 위 SHA-256 및 원본 스냅샷 해시 (`../data/audit_v1/measurements.parquet`)를 검사한 다음에만 `joblib.load`를 호출한다. 사용자 제공 pickle/joblib 파일은 읽지 않는다. 저장 manifest의 NumPy 2.0.2·scikit-learn 1.6.1과 [의존성 명세](../requirements.txt)의 joblib 1.5.1이 현재 `/usr/bin/python`(Python 3.9.25)에서 일치한다. `/usr/bin/python3`의 scikit-learn 1.5.2로 저장 모델을 로드해 운영하지 않는다.
2. 입력에는 `model_id`, 고정 revision, `workload_task`, 양의 유한한 `model_parameter_bytes`, batch, 최대 길이, 동시성 및 메타데이터 출처·획득 시각을 요구한다. 역사적 `result_json` 값을 요청 시점 입력으로 읽지 않는다. GPU dtype과 복제 수에 따라 파라미터 바이트가 달라지므로, 등록 값의 계산법·dtype이 로컬 CUDA 단일 replica 계측과 일치하지 않으면 보류한다. 모든 수치의 양수·유한성, 정수 필드, 단위를 API에서 검사한다.
3. 관측 피크에는 저장 함수의 `host=icns`, `device=cuda`, `device_name=NVIDIA RTX PRO 5000 Blackwell`, `gpu_indices_json=[0]`, `replica_count=1`, `cpu_core_limit=2` 검사를 그대로 적용한다. 조건부 모델은 학습 9개 `model_id`를 거부한다. 적재시간 모델은 프로필과 새 모델 여부를 자체 검사하지 않으므로 같은 게이트를 앞에 붙인다. `causal_lm_forward`는 donor 1개라 두 신호 모두 보류한다. `inference` donor는 8모델이며 파라미터 크기 범위는 `0.01634–0.46435 GiB`, 관측 요청 범위는 batch `1–16`, 길이 `32–512`, 동시성 `1–8`이다. 이 범위 밖을 기본 API에서 보류하는 추가 규칙은 제안이며 기존 평가의 커버리지 수치를 그대로 적용할 수 없다.
4. 반환 객체의 공통 상위 필드는 `hardware_profile`, `scope`, `source_sha256`, `metadata_provenance`, `artifact_hashes_verified_at_load`다. 각 신호에는 `status`, `estimate`, `unit`, `target_definition`, `training_relation`, `artifact_sha256`, `reasons`를 따로 둔다. 한 신호가 보류돼도 다른 신호를 숫자 0으로 대체하지 않는다. 8후보 평가기의 배치 p95/요청 처리량은 별도 결과로 둔다.

현재 저장 모델을 일치하는 `/usr/bin/python`에서 해시 확인 뒤 로드해, 기존 encoder 행의 결과 라벨을 지우고 **새 모델 ID로 바꾼 스모크 테스트**에서 두 함수가 양의 값을 반환했다. 이는 직렬화·입력 경로 확인일 뿐 새 모델 검증이 아니다.

### 제한적 GO의 구현

[오프라인 API](../scripts/assess_requirement_signals.py)는 [예시 요청](../examples/requirement_signals_request.json)을 받아 두 결과를 `signals.observed_cuda_reserved_peak_gib`와 `signals.cached_local_load_seconds`로 독립 반환한다. 실행은 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /usr/bin/python poc/optimization_research/scripts/assess_requirement_signals.py --input poc/optimization_research/examples/requirement_signals_request.json`이다. [예시 출력](../examples/requirement_signals_example_result.json)의 모델 ID·revision과 등록 정보는 **합성값**이며 숫자는 저장 모델의 직렬화·입력 경로 스모크 테스트다.

API는 저장 모델 2개와 스냅샷의 SHA-256, NumPy·pandas·scikit-learn·joblib 버전을 `joblib.load`보다 먼저 검사한다. 각 파일을 한 번 읽어 해시를 확인한 **동일한 바이트**로 원본 Parquet와 모델을 해석한다. 검사 뒤 경로의 파일이 교체되어도 다른 바이트를 파싱·역직렬화하지 않는다. 원본 주 캠페인에서 학습 모델 ID/revision/파라미터 크기와 64개 입력 조건을 읽어 지원 범위를 고정한다. `float32`와 `Σ numel × element_size` 계산법, 파라미터 수와 바이트 수의 일치, 요청 전 `available_at`, 고정 호스트·GPU 0·CPU 스레드/affinity 2, 작업 종류와 모델 크기 범위를 검사한다. GPU 피크는 주 캠페인에 관측한 `(batch, length, concurrency)` 조합에서만 예측한다. 새 모델 ID가 학습된 9개 revision 중 하나를 제출하면 대소문자를 정규화해 비교하고 `training_revision_claimed_under_new_model_id`로 **두 신호를 보류**한다. 이름 변경만으로 학습 내 모델을 새 모델로 표시하지 않기 위한 경계다. 지원 범위 밖 또는 학습 모델 ID라면 해당 신호를 `ABSTAIN`으로 내고, 로컬 적재시간 신호가 적격하면 별도로 답한다. 학습 모델 ID의 적재시간 숫자는 `IN_SAMPLE_EXPLORATORY`로 표시한다. 적재시간 ExtraTrees의 예측 worker는 메모리에서 1개로 설정해 병렬 합산 순서에 따른 마지막 자리 변화를 피한다. 저장 가중치와 파일은 바꾸지 않는다.

`metadata_provenance=caller_declared`는 핵심 경계다. 이 로컬 API는 사용자가 제출한 `source`·`available_at` 문자열의 **형식과 시간 순서**만 확인한다. 실제 등록 시점에 그 메타데이터가 존재했는지, 새 모델 ID가 다른 모델을 가리키는지 인증할 수 없다. 동일 revision의 이름 변경은 감지하지만, 새 revision이 같은 가중치를 담았는지는 확인할 수 없다. `DECLARED_UNSEEN_MODEL`도 독립 신규 모델 검증을 뜻하지 않는다. `artifact_hashes_verified_at_load=true`는 **로드할 때** 읽은 바이트의 해시를 확인했다는 뜻이며, 로드 후 Python 호출자가 가변 번들을 수정하지 않는다는 전제가 있다. [13개 경계 테스트](../tests/test_assess_requirement_signals.py)가 저장 아티팩트 스모크, 독립 보류, 학습 내 표시, 해시·버전 선검사, 해시 검사 뒤 경로 교체, 학습 revision 재명명 거부, 불명확한 등록 정보 거부를 확인한다.

2026-09-28 추가 경계 수정: 반환 객체가 모듈 전역 하드웨어 프로필 사전을 공유하던 문제를 제거했다. 이전에는 첫 결과의 `hardware_profile.cpu_core_limit`을 4로 바꾸면 다음 CPU 4 요청이 지원 범위 검사를 통과했지만, 실제 예측 입력은 여전히 CPU affinity 2였다. 반환 프로필을 깊게 복사하고 GPU 인덱스·복제 수·CPU 필드의 JSON 타입까지 정확히 비교한다. 따라서 `[false]`, `true`, `2.0`을 `[0]`, `1`, `2`로 받아들이지 않는다. 두 신호의 보류 사유 목록도 서로 분리했다. 저장 모델·추출 자료와 정상 예시의 예측값은 변경하지 않았다.

## 명시적 비주장과 다음 검사

관측 예약 피크는 **최소 VRAM, OOM 위험, GPU 호환성 또는 VM RAM 요구량이 아니다**. 9,591실행 중 GPU 성공 7,985개에서 OOM은 0건이고 실패 10건은 모델 파일 접근 오류다. 적재시간은 VM 부팅·이미지 다운로드·모델의 GPU 이동·서비스 준비·배포 완료 시간이 아니다. 두 모델은 독립 신규 호스트·GPU·모델군 검증을 통과하지 않았고, 사용자 지연 SLO·실제 스케일 전이 결과도 학습하지 않았다. [메모리 적합성](memory_feasibility_review.md), [연구→배포 명세 경계](research_to_deployment_spec_boundary.md).

**후속 검사:** [모델 전체 제외 예측의 지원 조건 재평가](requirement_signal_gate_impact.md)에서 GPU 예약 피크 답변은 주 캠페인 `512/575→384/575`, 새 입력·공간표본 조건은 각각 `0/18`·`0/44`로 줄었다. 남은 모델 평균 커버리지도 `95.12%→93.75%`로 낮아져, 현재 조건을 정확도나 안전 개선으로 해석할 수 없다. 이는 저장된 최종 모델이 아니라 각 대상 모델을 제외하고 학습했던 고정 fold 예측의 사후 분류다. 알려진 모델과 새 모델의 `training_relation`을 혼동하거나 `assess_gpu_shape`의 지원 범위를 확장하는 회귀를 계속 검사한다. 새 앱·하드웨어에서 신뢰 가능한 요구량 또는 메모리 적합성으로 승격하려면 실제 사전 등록 메타데이터와 자원 제한별 성공/OOM·SLO 측정이 별도로 필요하다.
