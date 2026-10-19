# 저장된 성능 예측기의 추론 worker 설정

> 이 시간표는 최초 전역 입력 범위 판정 시점의 역사적 측정이다. 여기서 `PREDICTED`였던 CPU 예시는 현재 [모델·장치별 지원 검사](saved_model_condition_support_fix.md)에서 `ABSTAIN`한다. 현재 벤치마크 스크립트는 이를 기록하고, 지원되는 CUDA 예시로 worker 수치 동등성을 검사한다. 아래 60회 시간표를 다시 측정한 것은 아니다.

2026-09-28. [오프라인 성능 예측 호출](../scripts/predict_performance.py)은 저장된 ExtraTrees가 `n_jobs=2`로 학습됐더라도 예측 직전에 `n_jobs=1`을 설정한다. 트리 재학습이나 모델 파일 변경 없이, 이 호출 경로의 작은 후보 질의에서 스레드 조정 비용을 줄인다. 저장된 모델은 같은 호스트·이미 본 모델의 배치 벤치마크 성능 예측용이며 배포·스케일링 실행 경로가 아니다.

## 검증

저장된 `performance_model.joblib`(SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`)을 한 번 로드하고, 이미 적합된 트리를 공유하는 독립된 1/2-worker 예측기만 만들었다. 주 캠페인의 적격 CPU/GPU 후보 **4,856행**에서 두 예측 배열의 최대 절대차는 **0**이었다. [실제 예측 API](../scripts/predict_performance.py)의 예시 입력도 `PREDICTED`를 반환했고, 2-worker 출력과 수치가 같았다. 학습용 [모델 생성 코드](../scripts/train_performance.py)는 `n_jobs=2` 그대로다.

| 예열한 `model.predict` 입력 | 1 worker 평균 / p95 | 2 workers 평균 / p95 |
| --- | ---: | ---: |
| 후보 1행 | **39.95 / 49.91ms** | 57.20 / 64.80ms |
| 후보 8행 일괄 | **43.37 / 50.19ms** | 57.87 / 65.48ms |

각 입력마다 20회 예열한 뒤 worker 순서를 섞어 각 60회 측정했다. 한 후보 평균은 이 한 번의 로컬 CPU 실행에서 약 **30.2%** 낮았다. 이는 `model.predict` 시간으로, 모델 로드·지원 범위 검사·DataFrame 생성·JSON 직렬화는 포함하지 않는다. 8행 일괄 수치는 현재 [일괄 호출 구현](performance_batch_predict_findings.md)의 모델 채점 부분만 재는 참고값이다. 따라서 이 표로 실제 플랫폼의 응답시간이나 8후보 API 전체 시간을 주장하지 않는다. [2단계 추론 실험](cascade_inference_findings.md)의 별도 모델·입력에서 얻은 시간도 이 경로의 수치로 전용하지 않았다.

재현: 연구 루트에서 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/benchmark_performance_predict_workers.py`. [벤치마크 코드](../scripts/benchmark_performance_predict_workers.py), [행별 집계 JSON](../artifacts/performance_predict_workers_v1/metrics.json). 현재 두 테스트 파일의 **8개 검사**가 통과했고, 저장 모델과 같은 Python 3.9·scikit-learn 1.6.1 환경에서는 `unittest` 집중 검사 8개가 통과했다. 현재 결과의 적용 범위는 저장된 연구용 모델과 이 하드웨어의 warm 추론이다.

이후 입력 계약 검사에서 아주 큰 정수(`10**1000`)가 `numpy.isfinite`의 타입 오류를 일으켜 지원 범위 밖의 요청이 `ABSTAIN`으로 끝나지 않는 문제를 수정했다. 현재 예측 호출은 유한성·정수성·학습 범위를 안전하게 확인하며, 같은 입력은 `missing_or_out_of_range`로 보류한다. 기존에 허용하던 NumPy `float64` 정수값은 계속 허용한다. 배열이 아닌 입력은 명시적으로 거부하고, 배열 안의 객체가 아닌 항목은 `record_must_be_object`로 보류한다. [계약 검사](../tests/test_data_contract.py)와 예측기·8후보 경로의 집중 테스트는 **20개 통과, 2개 건너뜀**이었다. 위 시간 측정값을 다시 산출한 것은 아니다.
