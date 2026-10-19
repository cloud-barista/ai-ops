# GPU 1개 계측으로 GPU 2개 처리량 병목을 분해할 수 있는가

## 결론

현재 SQLite의 `cpu_seconds`, `compute_seconds`, `load_seconds`, 실행 시간, CPU/GPU 사용률은 **추론 구간의 CPU 직렬 비율이나 GPU 점유 시간을 식별하지 못한다.** 따라서 GPU 1개 실행에서 Amdahl식 병목을 추정해 GPU 2개 처리량을 예측하는 방법은 이 자료에 맞지 않는다. 두 배 이하로 제한한 어떤 예측기도 기존 계측 조건부 ExtraTrees의 처리량 오차에 도달할 수 없다. 이 결론은 새 알고리즘의 실패 증거이자, 현재 계측 설계에서 물리 제약을 그대로 강제하면 생기는 오류를 확인한 것이다.

## 측정 프로토콜

수집기 구현 (`/path/to/aiops/khu_policy_agent/live_collector.py:1637`)은 GPU 2개 실행에서 두 자식 프로세스에 **같은 전체 `InferenceProfile`을 각각** 전달한다. 각 프로세스가 GPU 1개 실행과 같은 예제 수를 처리하며, 기록된 GPU 2개 처리량은 자식들의 `inference_examples_per_second`를 합한 값이다. 반면 `compute_seconds`와 `wall_seconds`는 자식 중 최댓값이고, `cpu_seconds`는 자식들의 합이다. 실제 자료에서도 세 캠페인 637쌍 모두 GPU 2개 실행의 예제 수가 GPU 1개 실행의 정확히 두 배이며, 기록된 GPU 2개 처리량은 자식 처리량의 합과 일치했다. **같은 총 작업을 한 GPU와 두 GPU에서 나눠 수행한 완료 시간 비교가 아니다.**

GPU 1개 실행의 `cpu_seconds / wall_seconds` 중앙값은 주 캠페인 **1.007**이지만 `compute_seconds / wall_seconds`는 **0.510**, `load_seconds / wall_seconds`는 **0.331**이다. 추론 실행 구현 (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py:636`)은 `cpu_seconds`를 전체 실행 시작부터 측정하므로, 로드와 그 밖의 구간을 포함한 CPU 시간을 추론 단계의 CPU 수요로 나눌 근거가 없다. GPU/CPU 사용률도 실행 전체의 노드 요약이다. GPU 2개 측정은 주 캠페인 575쌍 모두 GPU 1개 측정이 끝난 뒤 시작했다. 차이가 로드·워밍업·클록 상태·공유 CPU 경쟁 중 어디서 왔는지는 이 집계값만으로 구분할 수 없다.

| 캠페인 | GPU 1/2개 쌍 | GPU 2개 처리량이 2배 초과 | 처리량 비율 중앙값 |
| --- | ---: | ---: | ---: |
| 주 완전 그리드 | 575 | 523 | 2.297 |
| 새 matrix 조건 | 18 | 18 | 2.299 |
| 새 space 조건 | 44 | 42 | 2.149 |

[Amdahl의 원래 고정 작업 속도 향상 논의](https://doi.org/10.1145/1465482.1465560)를 이 복제 벤치마크의 처리량에 직접 적용할 수 없다. [NVIDIA의 Triton 운영 문서](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/optimization.html)도 복제 인스턴스의 이득은 모델과 부하에 따라 달라 실제 부하로 측정할 것을 권한다. 여기서는 GPU 2개 처리량이 2배를 넘는 이유를 특정하지 않는다.

## 두 배 상한의 오차 하한

같은 GPU 1개 실측 처리량 `R1`으로 `R2 = 2 × R1`을 예측했다. 더 관대한 비교로, 참값이 2배 이하면 완벽하게 맞히고 참값이 2배를 초과할 때만 2배에 걸리는 **상한 제한 예측기의 이론적 최저 오차**도 계산했다: `mean(max(log(R2) − log(2R1), 0))`. 이 오라클은 실현 가능한 예측기가 아니며, 두 배 이하라는 제한의 불가피한 오차만 표시한다. 기존 ExtraTrees 수치는 이전과 같은 모델별 완전 제외 학습의 저장된 예측에서 가져왔다. 모두 GPU 1개 실행은 관측하고 GPU 2개 결과는 평가에만 사용한다.

아래 값은 모델별 평균 절대 로그오차를 다시 평균한 값이다. 낮을수록 좋다.

| 분할·조건 | 단순 2배 | 2배 상한 오라클 하한 | 기존 정적 조건부 | 기존 계측 조건부, 샘플 수 제외 |
| --- | ---: | ---: | ---: | ---: |
| 모델별 제외, 주 575쌍 | 0.1724 | 0.1699 | 0.1120 | **0.0961** |
| 모델별 제외, 새 matrix 18쌍 | 0.1485 | 0.1485 | 0.1193 | **0.1152** |
| 모델별 제외, 새 space 44쌍 | 0.1697 | 0.1685 | 0.1707 | **0.1251** |
| BERT 4종 공동 제외, 주 256쌍 | 0.1608 | 0.1559 | **0.0952** | 0.0959 |
| BERT 4종 공동 제외, 새 matrix 8쌍 | 0.1182 | 0.1182 | **0.0878** | 0.0889 |
| BERT 4종 공동 제외, 새 space 20쌍 | 0.1511 | 0.1490 | 0.1155 | **0.1085** |

상한 오라클조차 계측 조건부 모델보다 모두 나쁘다. 그러므로 `R2 ≤ 2R1`을 강제하는 Amdahl식 후보에 GPU 1개 CPU/GPU 계측을 더해도 이 비교를 뒤집을 수 없다. 이 하한은 지연시간·실제 Scale-out 성공에 대한 판단이 아니며, BERT 4종 공동 제외에는 다른 encoder donor가 남는다.

2배를 넘어서는 보정 인자를 donor로 학습할 수는 있지만, 현재 자료의 로드 구간과 추론 구간을 분리할 수 없어 그것을 물리 병목 추정으로 해석할 근거가 없다. GPU 1개 계측을 넣어 상대 처리량을 직접 학습하는 [기존 실험](telemetry_conditioned_scaling_findings.md)과 실질적으로 같은 통계적 문제다. 따라서 이 방향을 경희대 고유 방법이나 효용이 검증된 새 Scale-out 알고리즘으로 승격하지 않는다. 단계별 CPU/GPU 시간과 순서를 교차한 반복 측정이 있어야 물리 원리를 다시 검토할 수 있지만, 현재는 SQLite만 사용 가능하다.

재현: `OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/audit_replica_bottleneck_identifiability.py`. [계산 코드](../scripts/audit_replica_bottleneck_identifiability.py), [수치·입력 해시](../artifacts/replica_bottleneck_identifiability_v1/metrics.json), [비교 예측기와 분할](telemetry_family_holdout_findings.md).
