# 연구용 GPU 형상 증거 계약의 구현 가능성

2026-09-28 현재 코드 기준의 **연동 제안**이다. 기관 간 API 또는 배포 승인 계약을 확정한 문서가 아니다. 입력은 [8후보 오프라인 평가기](../scripts/assess_gpu_shape.py)의 결과이며, 출력은 이노그리드·ETRI가 판독할 수 있는 별도 JSON 증거 객체다. 기존 Go 계약 (`../../deployment_agent/go/service-control-api/internal/agentcontrol/models.go`)에 이 필드가 이미 있다는 뜻은 아니다.

## 현재 계약과의 대조

평가기는 알려진 모델·호스트·입력 조건 하나에서 GPU 프로세스 복제본 `1/2` × 프로세스 CPU affinity `2/4/8/16`의 정확히 8개 후보를 평가한다. 출력은 배치 벤치마크 `latency_p95_ms`와 `examples_per_second / batch_size`로 만든 요청/초 **보정 전 점추정치**다. 후보 하나라도 학습 지원 범위 밖이거나 예측이 비정상이면 전체 선택을 `ABSTAIN`한다. 모든 후보가 예측 기준을 못 채워도 `ABSTAIN`한다. `RESEARCH_ESTIMATE`는 GPU 복제본 수, 그다음 CPU affinity 코어 수가 최소인 추정 합격 후보다. [기능 시연 결과](../artifacts/gpu_shape_assessment_v1/example_result.json)의 SLO는 시연용 입력이고 사용자 서비스 SLO가 아니다.

Go의 `ApplicationProfile.Requirements.SLO`는 `latency_p95_ms_max`, `throughput_rps_min`을 갖고, `ModelRecommendation.InferenceConfiguration`은 batch·동시성·`replicas`를 갖는다. 그러나 이름이 비슷해도 서비스 p95와 배치 p95, 서비스 replica와 같은 호스트 GPU 프로세스 복제본은 같은 측정 대상이 아니다. `ResourceRecommendation` 후보는 `candidate_id`, `feasible`, 완전한 `DesiredInfrastructure`(노드·VM CPU/메모리/스토리지·가속기), 점수와 자원 스냅샷을 갖는다. `DesiredDeploymentSpec`은 Guard를 통과한 판단·계획을 바탕으로 생성된다. Go 선택 검증 (`../../deployment_agent/go/service-control-api/internal/agentcontrol/service.go`)은 `node_count`, `cpu_cores_per_node`, 메모리·스토리지·가속기 최소치와 `feasible`을 확인한다. 판단 Guard (`../../deployment_agent/go/service-control-api/internal/agentcontrol/decision_runtime.go`)는 후보 ID가 실제 추천 후보에 존재하고 요구를 충족해야 `DEPLOY`를 승인한다. `ShadowPolicyAssessment`에는 모델 증거를 별도로 기록하는 경로가 있지만 `proposed_action`, `selected_candidate_id`, `guard_status`가 중심이므로 이 정적 벤치마크 예측을 그대로 대입할 필드는 아니다.

| 평가기 값 | Go의 유사 필드 | 안전한 연결 방식 |
| --- | --- | --- |
| `application_id`, `workload_id`, `model_id`, `model_revision`, `host`, `device_name` | `ApplicationProfile.AppID`, `ProfileID`, `SelectedModel.ModelVersion` | 연구 측 식별 조건으로 보존한다. 등록 응용·모델 버전·호스트와의 일치는 외부 식별자 매핑 및 검증이 있어야 한다. 현재 예시 `application_id`는 시연용 값이다. |
| `batch_size`, `max_length`, `request_concurrency`, `repeats` | `InferenceConfiguration.MaxBatchSize`, `MaxConcurrency`; 일부 응용 입력 맥락 | 벤치마크 프로토콜 조건으로 보존한다. `MaxBatchSize` 같은 서비스 설정으로 자동 대입하지 않는다. |
| `batch_latency_p95_limit_ms`, `min_requests_per_second` | `SLORequirements.LatencyP95MSMax`, `ThroughputRPSMin` | 별도 **배치 벤치마크 목표**로 보존한다. 사용자 SLO에서 왔다면 출처를 명시하되 동일한 관측치라고 간주하지 않는다. |
| `predicted_batch_latency_p95_ms`, `predicted_requests_per_second`, 여유값 | `ResourceScores.SLOHeadroom` | 단위가 붙은 예측·목표 차이로 보존한다. 보정/서비스 검증 없이 Go 추천 점수나 `feasible=true`로 변환하지 않는다. |
| `gpu_replicas`, `cpu_affinity_cores` | `DesiredInfrastructure.NodeCount`, `CPUCoresPerNode`, `Accelerator.Count`, `InferenceConfiguration.Replicas` | **동일 호스트 프로세스 실험 형상**으로 보존한다. VM 노드·GPU 할당량·배포 복제본을 뜻하지 않는다. |
| `status=RESEARCH_ESTIMATE/ABSTAIN`, 후보별 상태·사유 | `AutomationDecision.Action`, `ScalingDecision.Action` | 연구 증거 상태로 보존한다. `ABSTAIN`은 `REJECT/RETRY/KEEP` 명령이 아니고, `RESEARCH_ESTIMATE`는 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN` 명령이 아니다. |
| `model_sha256`, `source_sha256`, sklearn 버전 | `ShadowPolicyAssessment`의 artifact/dataset hash | SHA는 각각 저장 모델 파일과 SQLite에서 추출한 **Parquet 파일**의 해시다. 원본 SQLite 자체의 해시로 바꾸어 부르지 않는다. |

## JSON 계약 초안 `khu.research.gpu_shape_evidence` / `0.1.0`

아래 구조는 기존 평가 결과의 **판단 근거와 보류 사유를 보존하는 최소 객체**다. `schema_version`은 이 제안의 버전이며 Go `ContractVersionV1`이나 `DesiredDeploymentSpec.spec_version`과 무관하다. JSON 키 이름과 전달 위치는 기관 간 협의 전까지 연구 내부 초안으로 취급한다.

```json
{
  "evidence_type": "khu.research.gpu_shape_evidence",
  "schema_version": "0.1.0",
  "evidence_id": "sha256:4ad9c1363c16837de9dd449faeed4068da45f8e1419a014816a42244cd775b9a",
  "request_sha256": "00be631e11bcc3329b990aba134679a9f646083fb2bcdccd1441e6f6c1120a24",
  "generated_at": "2026-09-28T00:00:00Z",
  "status": "RESEARCH_ESTIMATE",
  "scope": {
    "kind": "same_host_batch_benchmark_prediction",
    "calibration": "uncalibrated_point_estimate",
    "deployment_authority": false
  },
  "workload": {
    "application_id": "research-demo-bert-l2",
    "workload_id": "783f9cfdcd98f2b1017e1329e7130ca0a35064f0f9fbcd583364ccf624e31d55",
    "model_id": "google/bert_uncased_L-2_H-128_A-2",
    "model_revision": "30b0a37ccaaa32f332884b96992754e246e48c5f",
    "device": "cuda",
    "host": "icns",
    "device_name": "NVIDIA RTX PRO 5000 Blackwell",
    "batch_size": 1,
    "max_length": 32,
    "request_concurrency": 1,
    "repeats": 8
  },
  "benchmark_objective": {
    "batch_latency_p95_limit_ms": 3.0,
    "min_requests_per_second": 600.0
  },
  "selection_rule": "min_gpu_replicas_then_cpu_affinity_cores",
  "selected_shape": {"gpu_replicas": 2, "cpu_affinity_cores": 2},
  "candidates": [
    {
      "gpu_replicas": 1,
      "cpu_affinity_cores": 2,
      "status": "PREDICTED",
      "predicted_batch_latency_p95_ms": 1.923522978551211,
      "predicted_requests_per_second": 538.2368390898409,
      "batch_latency_margin_ms": 1.076477021448789,
      "requests_per_second_margin": -61.76316091015906,
      "meets_estimated_benchmark_objective": false
    }
  ],
  "provenance": {
    "model_artifact_sha256": "7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161",
    "training_extract_parquet_sha256": "b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906",
    "artifact_hash_check": "assessor_reported_verified_at_load",
    "training_sklearn_version": "1.6.1",
    "runtime_sklearn_version": "1.6.1"
  }
}
```

예시는 필드 형식을 보여주려고 실제 출력의 **첫 후보만 축약**했다. 위 코드를 그대로 완전한 계약 객체로 사용하지 않는다. 스키마를 통과한 [전체 예시](../artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json)에는 8개 후보가 정확히 한 번씩 들어간다. 각 후보의 `status`는 `PREDICTED` 또는 `ABSTAIN`이며, `PREDICTED`일 때만 예측값·두 여유값·합격 여부를 넣는다. 후보 `ABSTAIN`에는 평가기의 `reasons`를 그대로 둔다. 전체 `ABSTAIN`에는 `selected_shape`가 없어야 하고 최상위 `reasons`가 있어야 한다. 입력 오류나 모델 파일 검사 실패처럼 후보 검사를 시작할 수 없는 경우에는 `workload`, `benchmark_objective`, `candidates`가 없을 수 있다. 대신 어떤 단계에서 멈췄는지 `reasons`와 `provenance`를 남긴다. 출처를 알고 있으면 부분 보류에는 최상위 `objective_source`로 남긴다. 실사용 JSON의 `evidence_id`는 원 요청의 정규 JSON SHA, 평가 결과, 명시 목표 출처를 다시 정규 직렬화한 SHA다. 생성 시각은 해시에서 제외한다.

목표의 출처를 아는 호출자만 `benchmark_objective.source`를 추가한다. 예를 들어 `{ "kind": "research_demo", "reference": "poc/optimization_research/examples/gpu_shape_request.json" }` 또는 합의된 사용자 요청 식별자를 사용할 수 있다. 현재 CLI 입력에는 출처가 없으므로 **추론해서 `user_request`라고 채우지 않는다**. 실제 기준 측정 기반 보정 방식을 쓰는 미래 버전에서는 기준 측정 ID·측정 시각·호스트와 보정 아티팩트 해시를 별도로 요구한다. 현재 저장 모델은 그런 보정 증거가 없으므로 `calibration`은 `uncalibrated_point_estimate`로 고정한다.

VRAM 피크·모델 적재시간 같은 다른 연구 신호는 **서로 다른 `evidence_type`**으로 정의해야 한다. 이 스키마의 `candidates`에 관측 GPU 예약 피크를 최소 VRAM처럼 합쳐 넣지 않는다. 특히 [저장된 조건부 VRAM 추정기](../scripts/evaluate_vram_conditional.py)는 자신의 학습 모델 ID를 예측 대상에서 거부하지만, 현재 8후보 성능 평가기는 알려진 학습 모델·호스트만 받는다. 따라서 두 저장 모델이 같은 응용에 대해 동시에 지원되는 형상 추천기를 이룬다고 주장할 수 없다. [다른 VRAM 상한 추정기](../scripts/evaluate_vram_upper.py)의 지원 규칙은 별개이므로 그 모델까지 동일하게 거부한다고 일반화하지 않는다.

`artifact_hash_check=assessor_reported_verified_at_load`는 [현재 평가기](../scripts/assess_gpu_shape.py)의 결과 JSON에 디스크 모델·Parquet SHA가 로드 전에 검증됐다고 기록됐다는 뜻이다. 변환기는 결과 JSON을 받을 뿐, 그 주장이나 메모리의 bundle·지원 규칙을 다시 검사하지 않는다. 해당 표시가 없으면 `not_verified`로 둔다. **실행 시점 보증이나 독립 attestation이 아니다.** 증거 생성 중에는 bundle을 읽기 전용으로 유지해야 한다. 운영 연동 전에는 별도 프로세스에서 로드→예측→증거 직렬화를 한 번에 수행하거나, 동일한 무결성 조건을 코드로 검증해야 한다.

## 빠진 필드와 구현 판정

현 출력에는 `generated_at`, 증거 ID, SLO 출처, 실행 코드 버전·해시, 지원 규칙의 별도 버전, 실제 기준 측정의 ID·시각이 없다. 생성 시각·증거 ID·코드 해시는 오프라인 변환 단계에서 계산할 수 있다. 지원 규칙 버전은 저장 bundle/평가기 코드와 함께 고정해야 한다. SLO 출처·실제 측정 참조는 현재 SQLite나 평가 결과만으로 생성할 수 없고, 외부 호출자/측정 시스템이 제공해야 한다.

- **GO — 연구 내부 오프라인 증거 JSON 변환**: [변환기·스키마·저장 모델 예시와 검증 결과](gpu_shape_evidence_v0.1.0_findings.md)로 구현했다. 평가기 결과에 버전·명시 단위·범위·생성 시각을 붙이고, 8후보·상태·사유·선택·아티팩트 출처를 보존한다. 실패 출력도 같은 증거 유형으로 직렬화한다. 동일 입력·동일 결과의 안정적 `evidence_id` 정규화 규칙, `ABSTAIN` 누락 필드 규칙, 수치 유한성 검사를 명세·테스트했다. SLO 출처는 호출자가 제공할 때만 넣는다.
- **NO-GO — 자동 `ResourceRecommendation`/`DesiredDeploymentSpec` 생성**: VM별 CPU/메모리/스토리지/GPU 용량, 노드 수, 가격·가용성, 자원 스냅샷, 등록 응용·모델 ID 매핑, 실제 서비스 지연 및 배포 효과가 없다. 현재 SQLite와 이 평가 결과만으로 채울 수 없다.
- **NO-GO — 배포·업데이트·스케일 판단 연결**: 평가 결과에는 운영 모니터링의 `available_at`, 배포/스케일 명령과 후속 결과, 클라우드 생성 가능성이 없다. `ShadowPolicyAssessment`도 행동 중심의 다른 계약이라 직접 재사용하면 의미가 섞인다. 별도 측정·기관 합의·Go Guard 연동 테스트가 필요하다.

현재 구현된 것은 **버전이 붙은 오프라인 연구 증거 산출물**까지다. 이노그리드가 어떤 요청과 함께 전달하고 ETRI가 어떤 부분을 참고할지는 아직 정해지지 않았다. 그 합의 전에는 기존 Go 입력·판단·Manifest 경로를 바꾸지 않는다.
