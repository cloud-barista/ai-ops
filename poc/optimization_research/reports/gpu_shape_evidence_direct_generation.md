# 저장 모델에서 8후보 연구 증거를 직접 생성

2026-09-28. 기존 경로는 [저장 모델 평가기](../scripts/assess_gpu_shape.py)의 결과 JSON을 파일로 저장한 뒤 [증거 변환기](../scripts/emit_gpu_shape_evidence.py)에 다시 넘겼다. 변환기는 결과의 수학적 일관성을 검사하지만 `hashes_verified=true`가 실제 모델 로드에서 나온 값인지는 독립 확인하지 못한다. [새 원스텝 호출](../scripts/generate_gpu_shape_evidence.py)은 **고정 SHA-256의 모델·SQLite 추출 Parquet 확인 → 같은 프로세스에서 8후보 추론 → 기존 스키마 증거 작성**을 연속 실행한다. 외부 결과 JSON 입력은 받지 않는다.

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/generate_gpu_shape_evidence.py \
  --request examples/gpu_shape_request.json \
  --objective-source examples/gpu_shape_objective_source.json \
  --generated-at 2026-09-28T19:46:08.278489Z \
  --output /tmp/gpu_shape_research_evidence.json
```

예시 요청은 **시연용** 배치 p95 `≤3ms`·벤치마크 요청 `≥600건/초`다. 같은 생성 시각에서 새 함수와 CLI의 JSON 전체가 [기존 저장 예시](../artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json)와 정확히 일치했다. `evidence_id`는 `sha256:4ad9c1363c16837de9dd449faeed4068da45f8e1419a014816a42244cd775b9a` 그대로다. 기존 평가 모델이나 증거 스키마는 재학습·변경하지 않았다.

고정 모델 해시가 다르면 역직렬화 전에 종료 코드 `2`로 실패하고 **새 출력 파일을 생성하지 않는다**. 정상적으로 로드했지만 지원 범위 밖인 모델이면 8후보 `ABSTAIN` 증거를 남기고 선택 형상은 생략한다. 출력 파일은 증거 생성·검사가 모두 끝난 뒤 같은 디렉터리의 임시 파일에서 원자적으로 생성한다. **이미 존재하는 출력 경로는 거부하고 덮어쓰지 않는다.** 따라서 입력 파일과 출력 경로가 같아도 원본이 보존된다. 실패 전에 있던 기존 파일은 그대로 남으므로 호출자는 종료 코드를 확인하고 요청별 고유 출력 경로를 사용해야 한다. 예측기가 요청한 8개와 다른 개수의 결과를 내면 평가기도 `prediction_count_mismatch`로 보류한다.

검증: 학습 환경 `/usr/bin/python`의 관련 [25개 테스트](../tests/test_generate_gpu_shape_evidence.py)가 통과했다. 실제 저장 모델의 전체 증거 동등성, CLI 출력, 지원 범위 밖 보류, 모델 해시·실행 라이브러리 버전 오류의 무출력, 입력·기존 출력 경로와의 충돌, 예측 결과 수 불일치를 검사했다.

이 개선은 **결과 파일을 사이에 두지 않는 연구 호출 경계**다. 2026-09-28 보강 후 모델 파일을 한 번 읽어 같은 바이트 스냅샷의 SHA-256을 확인하고 그 스냅샷을 역직렬화한다. 해시 확인과 로드 사이에 파일 경로가 교체되거나 원본 파일이 다시 쓰여도 다른 모델 바이트를 로드하지 않는다. [회귀 테스트](../tests/test_assess_gpu_shape.py)는 로드 직전에 파일이 바뀌는 경우를 검사한다. 다만 이 방식은 Python 프로세스 자체나 모델 실행의 진위를 인증하지 않는다. 출력은 한 호스트·알려진 모델의 보정되지 않은 배치 벤치마크 추정이다. GPU 복제본·CPU affinity는 VM 노드·CPU 할당량이 아니고, `RESEARCH_ESTIMATE`는 `DEPLOY`, `UPDATE`, `SCALE_OUT`, `SCALE_IN` 승인 값이 아니다. 신규 모델·호스트, 사용자 SLO, VM 형상 변경의 실제 결과는 별도 검증이 필요하다.
