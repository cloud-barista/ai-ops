# 저장된 모델을 이용한 8후보 GPU 형상 평가

2026-09-28. [오프라인 평가기](../scripts/assess_gpu_shape.py)는 **하나의 이미 알려진 모델·호스트·입력 조건**에 대해 GPU 복제본 1/2개 × CPU affinity 2/4/8/16코어의 여덟 후보를 평가한다. 저장된 성능 예측기 (`../artifacts/performance_v1/performance_model.joblib`)를 다시 학습하지 않고 [일괄 추론](performance_batch_predict_findings.md) 한 번으로 후보별 배치 p95 지연과 처리량을 예측한다. `examples_per_second / batch_size`를 요청/초 추정치로 사용한다. 입력의 배치 p95 상한과 최소 요청/초를 모두 만족한다고 **예측한** 후보 중 GPU 복제본 수, 이어서 CPU affinity 코어 수가 가장 작은 후보를 `RESEARCH_ESTIMATE`로 반환한다.

입력은 [예시 JSON](../examples/gpu_shape_request.json)처럼 `batch_latency_p95_limit_ms`, `min_requests_per_second`, `candidates`를 가진다. 후보에는 `application_id`, `workload_id`, 모델 ID·revision, 호스트·장치, batch 크기·입력 길이·동시성·반복 수, `replica_count`, `cpu_core_limit`, `cpu_threads`를 넣는다. 평가기는 정확한 8개 격자, 동일 응용·작업·모델·호스트·입력 조건, `cpu_threads=cpu_core_limit`, 양수 유한 SLO를 검사한다. 측정 결과값 등 예상 밖 필드는 거절한다. 저장 모델의 후보별 지원 범위 검사에서 하나라도 실패하거나, 예측 합격 후보가 없거나, 예측값이 유한한 양수가 아니면 `ABSTAIN`한다. 선택 후보가 있는 출력에는 각 후보의 예측값과 SLO 차이를 함께 기록한다.

2026-09-29 후속 입력 경계 수정으로 **최상위 필드도 위 세 개와 정확히 일치해야 한다.** 이전 기본 평가기는 후보 내부의 예상 밖 필드는 거절했지만 최상위 `price_usd_per_hour_max` 같은 제약은 무시하고 선택 결과를 반환했다. 이제 이런 제약이나 필수 필드 누락은 모델 채점 전에 `ABSTAIN`하며, 단건·다건·마이크로배치·v0.1 증거 생성 경로가 같은 검사를 사용한다. 가격·노드 수 자체를 평가하는 기능이 생긴 것은 아니다. 정상 예시와 저장 모델의 선택 결과는 그대로다.

실행 전에 모델 SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`, 원본 추출 Parquet SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`과 학습 환경의 scikit-learn `1.6.1`, NumPy `2.0.2`, pandas `2.3.0+4.g1dfc98e16a`, 검증 환경의 joblib `1.5.1`을 검사한다. 불일치 시 모델을 채점하지 않고 보류한다. 출력에는 이 검증 상태와 실제 입력의 모델·호스트·batch·길이·동시성·반복 수를 남긴다.
출력의 `hashes_verified=true`는 **해시를 확인한 모델 파일의 바이트 스냅샷을 로드했다는 뜻**이다. 로드한 Python bundle의 모델이나 지원 규칙을 이후 메모리에서 바꾸었는지 다시 검사하지 않는다. 연구 함수에 전달하는 bundle은 로드 후 읽기 전용으로 유지해야 한다.

## 실제 저장 모델 동작 확인

[예시 결과](../artifacts/gpu_shape_assessment_v1/example_result.json)는 기존 추출 자료의 한 8후보 작업군을 **기능 시연용**으로 사용한다. 예시 요청 JSON에 명시한 시연용 기준은 배치 p95 `3ms` 이하, 요청 `600건/초` 이상이다. 이 기준에서 GPU 복제본 `2개`·CPU affinity `2코어`가 선택됐다. 해당 모델 예측은 배치 p95 `2.029ms`, 요청 `1,022.313건/초`이며, 기준과의 차이는 `+0.971ms`·`+422.313건/초`다. 이 작업군은 학습·검증 분리된 새 시험 표본이 아니며, 이 수치가 실제 사용자 SLO를 충족한다는 증거가 아니다. 후보의 참값을 입력이나 선택에 쓰지 않았다.

재현 명령은 연구 루트에서 `/usr/bin/python scripts/assess_gpu_shape.py --input examples/gpu_shape_request.json`이다. 위 학습·검증 라이브러리 버전이 필요하다. `/usr/bin/python -m unittest discover -s tests -p 'test_assess_gpu_shape.py' -v`에서 현재 12개 테스트가 통과했다. 테스트는 격자·입력 불일치와 최상위 미지원·누락 필드, 지원 후보와 보류 후보의 혼합, 비정상 예측과 결과 개수 불일치에서 부분 선택 방지, 요청/초 단위 변환, 최소 형상 순서, 해시·버전 검사와 로드 직전 파일 교체를 포함한다.

이 출력은 **보정되지 않은 같은 호스트 배치 벤치마크 점추정**이다. 응용·작업 ID의 동일성은 요청자가 제공한 값으로 확인할 뿐, 저장 모델이 응용의 모든 의미나 실제 입력 내용을 이해하는 것은 아니다. `replica_count`는 벤치마크의 GPU 복제본 수이고 `cpu_core_limit`는 프로세스 CPU affinity다. 고정 추출 자료의 주 캠페인에서 GPU 1개 실행은 물리 인덱스 `[0]`만, GPU 2개 실행은 `[0,1]`만 측정했다. 현재 요청·결과 계약은 GPU 인덱스를 전달하거나 검사하지 않으므로, 같은 호스트의 다른 단일 GPU나 이종 장치 조합으로 결과를 일반화할 근거가 없다. VM 노드 수·가격·실제 GPU 가용성·Deployment Spec·`DEPLOY`/스케일 명령은 만들지 않는다. 예측 오차, 새 모델·새 호스트, 분산 서비스 지연, 실제 사용자 SLO 안전성을 검증하려면 별도 관측과 교정이 필요하다.
