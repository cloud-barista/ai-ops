# Philly GPU 위험 신호의 버전형 연구 증거

2026-09-28. [동결된 위험 신호 API](../scripts/assess_philly_gpu_risk.py)의 결과를 [전용 JSON 스키마](../contracts/philly_gpu_risk_evidence_v0.1.0.schema.json) `khu.research.philly_gpu_risk_evidence` / `0.1.0`으로 보존한다. [한 번에 생성하는 CLI](../scripts/generate_philly_gpu_risk_evidence.py)는 저장 모델 명세·정규화 원본·동결 코드의 SHA-256을 검사하고 같은 프로세스에서 예측과 증거 직렬화를 수행한다. [미사용 기계 검증](philly_machine_holdout_findings.md)의 점수를 새로 계산하거나 모델을 재학습하지 않는다.

평가기의 **모든 JSON 요청 결과**에는 원 요청을 키 정렬·공백 없는 UTF-8 JSON으로 직렬화한 `assessed_request_sha256`이 붙는다. 생성기는 원 요청에서 `request_sha256`을 다시 계산해 두 값이 다르면 중단한다. 따라서 같은 기계·시각을 유지한 채 과거 5분 사용률만 바꾼 요청에 이전 확률을 붙일 수 없다. `evidence_id`는 정규화한 `{request_sha256, result}`의 SHA-256이고 `generated_at`은 제외하므로 같은 평가 결과에는 같은 ID가 붙는다. 이 결속은 **우연한 요청·결과 혼합 검사**이며, 호출자가 거짓 계측이나 조작된 평가 JSON을 제시할 가능성까지 증명하지 않는다. JSON으로 표현할 수 없는 값이나 NaN은 증거로 만들지 않고 오류로 끝낸다. `generated_at`은 기본으로 실행 시 UTC를 쓰며, 예시 재현을 위한 수동 지정은 실제 실행 시각의 증명이 아니다.

증거는 사건 정의, 30분 지평, 출처, 통합 로지스틱과 Beta 기준선 확률, 평가 상태·보류 이유, 모델·코드·원본 해시를 보존한다. `input_observation_verified=false`와 `scope.deployment_authority=false`는 항상 명시한다. 창 값만 잘못되어 보류한 경우에도 유효한 기계·GPU 슬롯·판단 시각은 `decision_context`에 남긴다. 유효한 판단 시각보다 앞선 생성 시각은 추정·보류 양쪽에서 거부한다. 식별자나 출처 자체가 잘못되면 그 컨텍스트를 복사하지 않고 요청 해시와 이유만 남긴다. `ABSTAIN`에서는 두 확률이 `null`이다. 스키마는 VM ID, 노드 수, `DEPLOY`·`SCALE_OUT` 같은 행동 필드를 허용하지 않는다. Go Flow 또는 `DesiredDeploymentSpec`에는 연결하지 않았다.

```bash
cd /home/researcher/ai-mcmp
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  poc/optimization_research/scripts/generate_philly_gpu_risk_evidence.py \
  --request poc/optimization_research/examples/philly_gpu_risk_request.json \
  --output /tmp/philly_gpu_risk_evidence.json
```

[고정 요청](../examples/philly_gpu_risk_request.json)의 [예시 증거](../examples/philly_gpu_risk_evidence_example.json)는 저장된 [평가기 결과](../examples/philly_gpu_risk_example_result.json)와 일치한다. `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python -m unittest discover -s poc/optimization_research/tests -p 'test_*philly_gpu_risk*.py' -v`에서 관련 **28개 테스트가 통과**했다. 테스트는 저장된 holdout 예측의 두 확률, 요청 해시와 안정적 ID, 잘못된 창의 보류, 같은 식별자의 변경된 창을 이전 결과와 짝지을 때의 거부, 비정상 확률·출처·스키마 및 해시 오류에서의 무출력을 확인한다. 실제 분별 계측과 수신 시각은 호출자의 선언이며, 이 확률은 사용자 SLO·VM 증감 효과·배포 승인을 나타내지 않는다.
