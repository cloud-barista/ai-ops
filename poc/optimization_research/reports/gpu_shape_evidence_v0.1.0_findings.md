# 8후보 연구 증거 JSON 0.1.0 구현

2026-09-28. [계약 가능성 검토](research_evidence_contract_feasibility.md)의 오프라인 범위를 [변환기](../scripts/emit_gpu_shape_evidence.py)와 [Draft-7 JSON Schema](../contracts/gpu_shape_evidence_v0.1.0.schema.json)로 구현했다. 변환기는 [형상 평가기](../scripts/assess_gpu_shape.py)의 원 요청·결과 쌍을 검사하고 `khu.research.gpu_shape_evidence` / `0.1.0` 객체를 만든다. 기존 Go `ResourceRecommendation`, `DesiredDeploymentSpec`, 배포·스케일 행동은 만들지 않는다.

## 실제 저장 모델의 예시

[시연용 요청](../examples/gpu_shape_request.json)과 [저장된 평가 결과](../artifacts/gpu_shape_assessment_v1/example_result.json), 호출자가 명시한 [`research_demo` 목표 출처](../examples/gpu_shape_objective_source.json)로 [예시 증거 JSON](../artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json)을 생성했다. JSON Schema 검사를 통과했고, 8후보 결과가 모두 남았다. 상태는 `RESEARCH_ESTIMATE`, 선택은 **같은 호스트 GPU 프로세스 복제본 2개·CPU affinity 2코어**다. 배치 p95 예측 `2.029ms`, 추정 요청 `1,022.313건/초`는 저장 모델의 원 결과와 같다. 목표 `3ms`·`600건/초`는 시연용이다. 실제 서비스 SLO, VM 노드 수·GPU 배정량 또는 배포 성공의 증거가 아니다.

원본 요청 정규 JSON의 SHA-256은 `00be631e11bcc3329b990aba134679a9f646083fb2bcdccd1441e6f6c1120a24`다. `evidence_id`는 `{request_sha256, result, optional_objective_source}`의 UTF-8 정규 JSON을 SHA-256으로 계산해 `sha256:`을 붙인다. 생성 시각은 제외하므로 같은 요청·평가 결과·명시 출처에는 같은 ID가 나온다. 예시 ID는 `sha256:4ad9c1363c16837de9dd449faeed4068da45f8e1419a014816a42244cd775b9a`다. 목표 출처를 바꾸면 ID도 달라진다. 생성 시각을 바꾸어도 ID는 같다.

원 결과의 `hashes_verified=true`는 **평가기에서 디스크 모델·추출 Parquet를 로드 전에 검사했다고 보고한 값**이다. 변환기는 해당 JSON을 독립적으로 인증하지 않고 `assessor_reported_verified_at_load`로 기록한다. 모델을 로드한 뒤 Python bundle을 바꾸었는지는 확인할 수 없다. `hashes_verified=false`는 `not_verified`로 기록한다. 원본 SQLite의 해시로 바꾸어 부르지 않는다.

## 보류와 실패 처리

- 입력 검사나 모델 검사 단계의 전체 `ABSTAIN`은 작업 조건·후보 배열이 없는 **부분 결과**로 보존한다. 출처를 명시했다면 이 경우 최상위 `objective_source`에 둔다. 작업 조건이나 예측값을 꾸며 넣지 않는다.
- 후보별 보류가 있는 전체 `ABSTAIN`은 8개 상태·사유와 최상위 `unsupported_candidates:<indices>` / `invalid_predictions:<indices>`를 대조한다. 여덟 후보 모두 예측됐으나 목표에 못 미친 경우만 `no_candidate_meets_estimated_slo`를 허용한다.
- 선택된 결과에 보류 후보가 있거나, 선택 형상이 실제 최소 합격 후보와 다르거나, 후보 순서·형상·SLO 차이 계산이 맞지 않으면 증거를 내지 않는다. NaN·Infinity, 알 수 없는 필드, 잘못된 출처·날짜와 스키마 위반도 거절한다.
- 후보 값은 배치 p95 `ms`, 처리량 `requests/s`, CPU affinity `cores`, 같은 호스트 GPU 프로세스 `replicas`로 분리했다. 점추정의 합격은 `meets_estimated_benchmark_objective`로 표현한다. Go의 VM `feasible`, `node_count`, 배포 `action`은 스키마에 없다.

## 재현

연구 루트에서 학습 당시 scikit-learn `1.6.1`이 설치된 Python으로 실행한다. `jsonschema==3.2.0`을 [연구 의존성](../requirements.txt)에 고정했다.

```bash
python scripts/emit_gpu_shape_evidence.py \
  --request examples/gpu_shape_request.json \
  --result artifacts/gpu_shape_assessment_v1/example_result.json \
  --objective-source examples/gpu_shape_objective_source.json \
  --generated-at 2026-09-28T19:46:08.278489Z \
  --output artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json
python -m unittest discover -s tests -p 'test_gpu_shape_evidence.py' -v
```

[계약 테스트](../tests/test_gpu_shape_evidence.py)는 저장 모델을 실제 로드해 저장 결과와 전체 값이 같은지, 정규 해시·출처·단위·8후보가 유지되는지, 후보/전체 보류와 수치·형상·선택의 변조를 거절하는지 검사했다. `/usr/bin/python` (scikit-learn `1.6.1`)에서 **6개 통과**했다. `/usr/bin/python3` (scikit-learn `1.5.2`)에서는 저장 모델 테스트 1개를 건너뛰고 나머지 **5개 통과**했다. 저장 모델을 새 버전의 scikit-learn으로 역직렬화해 검증한 것으로 해석하지 않는다.

예시 증거 파일 SHA-256은 `07a0aa88a00685941541a49b33fd9587e86c27d23d32a2f2c2173b4a7c93b067`이다. 위 재현 명령의 시각을 고정해 생성한 파일과 바이트 단위로 같다. 변환기 SHA-256 `9db6ee793c2f8fc1f821ebe001d7b2fcf6c276fcdd699a9db062399cf55bcb56`, 스키마 SHA-256 `e0feb360cdd780c86ad6358b84338470376cae04662e2d61c11b90ef59cf8b78`다.

이 산출물은 **오프라인 증거 형식과 일관성 검사**를 확인한다. 증거의 `gpu_replicas`에는 물리 GPU 인덱스가 없다. 원 자료는 1개일 때 `[0]`, 2개일 때 `[0,1]`만 측정했으므로, 이 증거로 다른 단일 GPU나 이종 장치의 성능을 주장할 수 없다. 기관 간 전달 API, 실제 사용자 SLO와 배치 측정의 대응, 신규 모델·호스트의 오차 보정, VM 자원 형상 및 운영 행동의 효과는 여전히 검증되지 않았다. Go 계약에는 아직 이 증거 객체의 필드나 연결점이 없다.
