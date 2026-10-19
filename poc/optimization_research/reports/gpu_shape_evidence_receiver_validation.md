# 8후보 연구 증거의 독립 수신 검증

2026-09-28. [증거 생성기](../scripts/emit_gpu_shape_evidence.py)는 원 요청과 평가 결과의 관계를 검사한다. 그러나 [JSON Schema 0.1.0](../contracts/gpu_shape_evidence_v0.1.0.schema.json)만 다시 적용하면 값의 타입은 확인해도 **선택 형상과 후보 결과의 관계**는 확인하지 못한다. 예시 증거에서 선택을 GPU 복제본 1개·CPU affinity 2코어로 바꿔도 스키마는 통과한다. 그 후보의 처리량 예측은 약 538건/초여서 시연 목표 600건/초에 못 미친다. 여덟 후보 중 한 형상을 중복시켜도 스키마는 통과한다.

[독립 검증기](../scripts/validate_gpu_shape_evidence.py)는 기존 스키마 검사 뒤 다음을 검사한다.

- GPU 복제본 `1/2` × CPU affinity `2/4/8/16`의 8개 형상이 정확히 한 번씩 있고, `cpu_threads`가 affinity 코어 수와 일치한다.
- 예측 후보의 배치 p95·요청/초 값과 목표에서 두 여유값 및 합격 여부를 다시 계산한다. 부동소수 비교는 기존 생성기의 허용 오차를 그대로 사용한다.
- 모든 후보가 예측됐고 합격 후보가 있으면 그중 GPU 복제본 수, 이어서 CPU affinity 코어 수가 최소인 형상만 `RESEARCH_ESTIMATE` 선택으로 인정한다.
- 후보 보류가 있으면 전체 `ABSTAIN`과 후보 인덱스 사유를 대조한다. 모두 예측됐으나 아무도 목표를 통과하지 못한 경우에는 `no_candidate_meets_estimated_slo`만 인정한다.
- 생성 시각도 기존 생성기처럼 UTC RFC3339여야 하며, 다른 시간대 오프셋은 거절한다.
- 작업 식별 문자열, 목표 출처, 보류 사유는 공백만으로 채울 수 없다. 생성기의 해당 입력 규칙을 다시 적용한다.

증거 파일만 받으면 `request_sha256`과 `evidence_id`는 **형식만** 확인할 수 있다. `request_sha256`은 원 요청 전체를, `evidence_id`는 원 요청 해시·원 평가 결과·목표 출처를 해시하기 때문이다. 검증기에 원 요청과 원 결과를 함께 주면 기존 생성 함수로 전체 증거를 다시 만들어 두 해시와 모든 필드를 비교한다. 이는 **주어진 파일끼리의 일치 검사**이며, 원 결과가 실제 고정 모델 실행에서 나왔다는 인증이나 모델 아티팩트 검증은 아니다. 목표 출처는 증거 객체에서 읽으므로, 출처와 `evidence_id`를 함께 바꾼 경우 원 요청·원 결과만으로 출처의 진위를 확인할 수 없다. 출처 확인에는 별도의 신뢰 가능한 입력이 필요하다.

```bash
cd /home/researcher/ai-mcmp/research
python3 scripts/validate_gpu_shape_evidence.py \
  --input artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json
python3 scripts/validate_gpu_shape_evidence.py \
  --input artifacts/gpu_shape_evidence_v0_1_0/example_evidence.json \
  --request examples/gpu_shape_request.json \
  --result artifacts/gpu_shape_assessment_v1/example_result.json
```

둘 다 성공하면 `gpu_shape_evidence_valid`를 출력한다. 검사 실패는 종료 코드 `2`다. [회귀 테스트](../tests/test_validate_gpu_shape_evidence.py)는 저장 예시·원본 쌍, 잘못된 선택·중복 형상, 수치/목표 불일치, 최소 형상 위반, 보류 사유, UTC 시각, 공백 문자열, 원 요청/증거 해시 변조를 검사한다. 새 테스트 **9개가 통과**했다. 전체 연구 테스트는 **613개 통과, 15개 건너뜀**이었다. 건너뛴 항목은 모두 저장 모델 실행에 필요한 학습 당시 scikit-learn 또는 다른 고정 네이티브 라이브러리 버전과 현재 Python 환경이 다른 경우다.

이 검증기는 연구용 배치 벤치마크 증거만 받는다. `RESEARCH_ESTIMATE`를 VM 자원 추천이나 배포·스케일 명령으로 바꾸지 않는다.
