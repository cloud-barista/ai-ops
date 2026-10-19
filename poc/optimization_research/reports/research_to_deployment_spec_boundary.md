# 연구 예측값과 Deployment Spec의 연결 경계

2026-09-28 현재 구현을 기준으로 한 연동 초안이다. 기관 간 API 합의나 배포 승인 규칙을 확정하는 문서는 아니다. [연구용 성능 예측 함수](../scripts/predict_performance.py), [8후보 평가기](../scripts/assess_gpu_shape.py), [소수 기준 측정 방법](anchor_residual_transfer_method_v1.md)은 배치 벤치마크의 CPU/GPU 후보별 지연·요청 처리량을 추정한다. 기존 Go 계약 (`../../deployment_agent/go/service-control-api/internal/agentcontrol/models.go`)은 완전한 `DesiredInfrastructure`를 포함한 `DesiredDeploymentSpec`을 Go Guard 통과 뒤 생성한다. 연구 추정치를 완전한 인프라 형상으로 변환하려면 아직 없는 관측과 명시 요구가 필요하다.

| 연구 결과·입력 | 기존 Go 필드 | 현재 연결 판단 |
| --- | --- | --- |
| 모델 ID·revision, batch·길이·동시성 | `SelectedModel`, `InferenceConfiguration`, `ApplicationRequirements.SLO` | 등록된 응용·사용자 요구의 식별 및 조건을 맞추는 키다. 예측기가 응용 ID나 사용자 SLO를 새로 만들지 않는다. |
| `predicted_batch_p95_ms`, 벤치마크 `predicted_request_rps` | `ResourceScores.SLOHeadroom` 또는 판단 근거 | 같은 호스트·지원 범위의 **연구용 추정치**로 따로 전달할 수 있다. 배치 p95를 사용자 end-to-end p95로 바꾸거나 `ResourceCandidate.Feasible=true`의 근거로 단독 사용하지 않는다. |
| `cpu_core_limit` | `CPUCoresPerNode`, `CPUCoresMin` | 측정값은 프로세스 CPU affinity 한도다. VM CPU 할당량과 동등하다는 근거가 없어 자동 대입하지 않는다. |
| `replica_count=1/2` | `NodeCount`, `Accelerator.Count`, `InferenceConfiguration.Replicas` | 같은 호스트에서 독립 GPU 프로세스 수를 잰 값이다. VM 노드 수·클라우드 GPU 개수·서비스 replica의 실제 확장 효과로 자동 대입하지 않는다. |
| 관측 GPU 예약 피크, Google 관측 CPU/RAM | `MemoryMiBPerNode`, `MemoryMiBMin`, `Accelerator.MemoryMiBMinPerDevice` | 최소 안전 메모리·OOM·형상 전환의 정답이 아니어서 자동 대입하지 않는다. |
| 실행 결과 JSON의 `model_parameter_bytes` | 등록 모델 크기·메모리 요구 | 기존 SQLite에서는 같은 Qwen 모델도 CPU FP32와 CUDA FP16의 바이트 수가 다르고 복제 실행에서는 합산된다. 등록 시점의 모델 메타데이터로 같은 정의·dtype을 확보하기 전에는 정적 요구량으로 대입하지 않는다. |
| 모델 예측 또는 합성 SLO 성공 | `CostEfficiency`, `Availability`, `SelectedCandidateID`, `Operation` | 가격·가용성·실제 CSP/리전/VM ID 및 실행 효과가 없으므로 값을 만들지 않는다. |

현재 [오프라인 평가기](../scripts/assess_gpu_shape.py)는 입력 조건·호스트/장치, 후보별 **벤치마크 예측값과 단위**, 보류 이유, 원본 데이터·모델 해시와 실행 라이브러리 버전을 출력한다. 기관 간 연동용 증거 객체를 정의할 때는 여기에 모델 버전, 예측 시각, 실제 기준 측정을 썼다면 그 측정의 식별자·시각, 훈련 지원 범위를 추가해야 한다. 이 증거는 Go의 `ResourceRecommendation`·`DesiredDeploymentSpec`과 별도로 둔다. 부재한 필드를 0 또는 추천 점수로 채우지 않는다. 후보가 연구 지원 범위 밖이면 추정 결과를 `ABSTAIN`으로 표시한다.

**현재 Mock 추천 점수의 의미:** Go 카탈로그 추천기 (`../../deployment_agent/go/service-control-api/internal/agentcontrol/resource_recommender.go`)는 `Scores.SLOHeadroom`을 실제 지연·처리량이나 `ApplicationRequirements.SLO`에서 계산하지 않는다. `0.5 + 0.5 × ResourceFit`으로 채우고 `Total`에 20%를 반영한다. 같은 최소 CPU/GPU 요구에서 사용자 SLO만 바꾸면 이 점수와 순위가 그대로다. 이 숫자는 **SLO 여유의 실측 또는 학습 예측 근거가 아니라 Mock 자원 적합도 대리값**이다. `ResourceCandidate.Feasible`도 자원 최소 조건의 충족 여부이며 사용자 p95·요청 처리량 적합성을 증명하지 않는다. 연구 증거를 연결할 때 이 두 필드만으로 SLO 적합 또는 배포 승인이라고 표시하지 않는다. 이 항목은 향후 기관 간 점수 의미·결측 표현을 합의할 때 수정해야 할 기존 PoC 계약 제한이다.

실제 `DesiredDeploymentSpec`은 기존 Guard·Adapter 경계 (`../../deployment_agent/docs/design/integration_boundary.md`)를 따른다. 사용자 요구, ETRI 자원·가격·가용성 원본, Innogrid 응용 상태·실행 결과가 연결된 뒤에만 Go 경로가 완전한 형상과 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 판단을 구성한다. [연구 데이터 계약](scale_effect_data_contract_v1.md)의 `available_at`·출처·명령 결과가 없는 현재 측정 파일만으로는 운영 판단 검증이 끝나지 않았다. 향후 기관 간 합의에서 별도 증거 객체의 정확한 스키마와 전달 위치를 정하고, 그때 Go/Adapter 계약 테스트를 추가한다.
