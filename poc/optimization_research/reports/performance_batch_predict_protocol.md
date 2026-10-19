# 저장된 성능 예측기의 8후보 일괄 채점 규약

2026-09-28. 점수 측정 전에 고정한다. 연구용 `predict_records`는 한 요청의 8개 CPU/GPU 형상을 입력받을 수 있지만 종전에는 각 형상을 별도로 `model.predict`했다. 현재 저장된 ExtraTrees를 다시 학습하지 않고, 지원 범위 검사는 후보별로 유지하면서 유효한 형상만 한 번에 채점한다.

- 모델은 `artifacts/performance_v1/performance_model.joblib` SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`이다. 원본 측정은 `data/audit_v1/measurements.parquet` SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다. 두 파일은 읽기 전용이다.
- 시각·성과 값을 보고 고르지 않고 `khu-hf-local-exhaustive`의 적격 `cuda` 행에서 첫 `workload_group=783f9cfdcd98f2b1017e1329e7130ca0a35064f0f9fbcd583364ccf624e31d55`를 사용한다. GPU 1/2개 × CPU affinity 2/4/8/16의 8행이며 현재 저장 모델의 지원 검사를 모두 통과한다.
- 비교는 같은 8개 레코드를 `predict_records(bundle, records)`로 한 번 호출한 경우와 `predict_records(bundle, [record])`를 8번 호출한 경우다. 두 경로의 상태·보류 이유·예측 숫자·순서를 정확히 비교한다. 별도 혼합 지원/보류 단위 테스트는 모델 호출 1회와 출력 순서를 검사한다.
- 저장된 모델의 추론 worker는 두 경로 모두 1개다. 같은 프로세스에서 각 경로를 10회 예열하고, 고정 난수로 순서를 섞어 각 60회 `perf_counter_ns`를 측정한다. 평균·중앙·95백분위 전체 함수 호출시간을 보고한다. 모델 로드는 타이머에서 제외하고, 지원 검사·DataFrame 생성·모델 채점·결과 구성은 포함한다.
- 산술적으로 기대하는 속도 이득과 별개로, 채택 조건은 출력 완전 동등성과 8후보 p95가 2배 이상 빠른 것이다. 실패하면 기존 직렬 경로의 품질 결과를 유지하고 성능 주장을 보류한다. 이는 로컬 warm 오프라인 예측 경로이며 실제 배포·스케일링 실행시간이 아니다.
