# 서로 다른 작업 조건 묶음 추론의 중복 검증 제거

2026-09-29. [변경 전 고정한 규약](cross_workload_microbatch_v2_protocol.md)에 따라 [v1 묶음 평가](cross_workload_microbatch_findings.md)의 요청별 중복 검증과 후보 격자 직렬화를 줄였다. [v2 구현](../scripts/assess_gpu_shape_microbatch_v2.py)은 한 호출에서 요청마다 유효성을 한 번 검사하고, 같은 후보 격자의 키를 재사용한다. 결과에 노출되는 가변 보류 사유 목록만 요청별로 복사한다. 저장 성능 모델은 재학습하거나 변경하지 않았다.

## 동등성과 시간

고정한 45개 작업 조건·1,125개 합성 SLO 요청의 **최종 JSON 전체**가 v1과 같았다. 격자와 두 경로의 실행 순서를 섞은 5쌍도 모두 같았다. [측정 원본](../artifacts/cross_workload_microbatch_v2/results.json)의 중앙 전체 처리 시간은 v1 **505.251 ms**, v2 **303.448 ms**로 **1.665배** 빨랐다. 사전 목표인 1.20배를 넘었다. 모델·원본 파일 적재와 첫 예열은 측정에서 제외했다. 정상 경로 한 프로세스의 CPU 처리 시간이며 서비스 요청 지연이나 VM 배포시간은 아니다.

[전체 격자 동등성](../artifacts/cross_workload_microbatch_v2/exhaustive_parity.json)도 확인했다. 주 CUDA 캠페인의 **575개 조건·4,600개 후보**에서 기존 8후보 평가기와 최종 JSON 차이가 없었다. 결과는 `RESEARCH_ESTIMATE` 355건, `ABSTAIN` 220건이다. 잘못된 요청, 지원 범위 밖 후보, 격자 순서 변경, 중복 요청, 합친 후보만 실패하는 예측기에서 격자별 재시도하는 경계도 테스트했다. GPU 형상 관련 **51개 테스트**가 통과했다.

v1 후속 탐색에서 배치 8보다 큰 배치가 더 빨랐지만, 그 결과로 기존 사전 선택값 8을 바꾸지 않았다. v2는 **고정 배치 8**에서만 비교했다. 이 9개 모델은 모두 저장 예측기의 학습에 이미 쓰였고 합성 SLO·시간 측정에도 노출됐다. 이 결과는 새 AI 모델의 요구 형상 정확도나 실제 배포·Scale-out/in 효용의 검증이 아니다. 비용 절감은 같은 저장 모델에 여러 연구 질의가 한꺼번에 들어올 때의 계산량에 한정된다.

재현: 연구 루트에서 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python scripts/benchmark_cross_workload_microbatch_v2.py`를 실행한다. 전체 격자는 `scripts/verify_gpu_shape_microbatch_exhaustive.py --implementation v2 --output artifacts/cross_workload_microbatch_v2/exhaustive_parity.json`로 확인한다.
