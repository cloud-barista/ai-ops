# GPU 복제 측정값의 의미

## 확인한 실행 방식

주 캠페인의 `replica_count=2`는 **같은 호스트의 물리 GPU 두 개에 프로세스를 하나씩 띄우고, 각 프로세스가 동일한 `InferenceProfile` 전체를 실행**한 기록이다. 수집기는 각 프로세스의 `inference_examples_per_second`를 더해 처리량으로 저장하고, 배치 지연 p95·로드·계산·wall 시간은 두 프로세스 값의 최대를 저장한다. 복제 실행·집계 코드 (`/path/to/aiops/khu_policy_agent/live_collector.py`)

원본의 `examples_per_second`는 **샘플/초**다. 이 연구의 배치 요청 처리율은 이를 `batch_size`로 나눈 값이며, 별도 lifecycle ETL의 `throughput_rps`와 단위를 확인하지 않고 합치지 않는다.

동결 스냅샷 `measurements.parquet` (`../data/audit_v1/measurements.parquet`) (SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`)의 주 캠페인에서 동일 모델·입력 조건·CPU affinity 한도의 GPU 1개/2개 **2,300쌍**을 대조했다. 모든 쌍에서 GPU 2개의 `examples`는 GPU 1개의 정확히 두 배였고, 저장된 합산 처리량은 더 높았다. GPU 2개의 wall 시간은 모든 쌍에서 더 길었고, 로드 시간도 모두 더 길었다.

| 비교 | 2,300쌍 중 해당 건수 |
| --- | ---: |
| GPU 2개가 GPU 1개의 정확히 두 배의 `examples` 처리 | 2,300 |
| 합산 GPU 2개 처리량이 GPU 1개 처리량보다 큼 | 2,300 |
| GPU 2개 wall 시간이 GPU 1개보다 짧음 | 0 |
| GPU 2개 로드 시간이 GPU 1개보다 짧음 | 0 |
| 합산 GPU 2개 처리량이 GPU 1개의 두 배보다 큼 | 1,750 |

## 연구에 미치는 영향

합산 요청/초는 **두 독립 replica의 벤치마크 처리 용량을 근사한 값**이다. 동일한 총 요청을 GPU 두 개에 나눠 처리했을 때의 완료 시간, 실제 라우터를 거친 서비스 처리량, VM 두 대로 Scale-out한 후의 처리량이 아니다. 두 replica의 p95 중 최댓값도 실제로 라우팅한 온라인 요청의 p95와 다르다. 따라서 `GPU2/GPU1 > 2`라는 비율에 고정 작업의 초선형 속도 향상이나 GPU 추가의 인과 효과라는 뜻을 부여할 수 없다.

현재 GPU 1개에서 2개로 가는 예측 실험의 목표는 **같은 호스트에서 동시에 실행한 replica 두 개의 합산 벤치마크 처리량**으로 표기한다. 실제 `SCALE_OUT` 정책 또는 배포 후 응답시간·비용 이득을 주장하려면 요청을 두 replica로 라우팅한 동일 총 부하의 반복 측정과 VM 생성·배포 시간 및 상태 기록이 필요하다. 현 SQLite에는 이 결과가 없다.

재현: [집계 검사 코드](../scripts/audit_replica_semantics.py), [스냅샷·수집 코드 해시와 건수](../artifacts/replica_semantics_v1/metrics.json). 모든 2,300개 GPU 2개 결과의 개별 replica JSON에서 처리량 합계와 지연·시간의 최대값을 확인한다.
