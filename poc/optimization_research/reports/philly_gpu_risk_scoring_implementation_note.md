# Philly GPU 위험 평가: 채점 실행 전 구현 고정

**2026-09-28 13:16:33 UTC, 실제 모델 적합·시험 점수 계산 전.** [동결 규약](philly_gpu_risk_protocol_frozen.md) SHA256 `c0b742cde3ce91c082b94e56275c33c34c159f4c22aa36952ae6f00685401621`과 [점수 전 표본 게이트](philly_gpu_risk_dry_run_gate.md)에 맞춰 채점 코드를 검토했다. [dry-run 감사 JSON](../artifacts/philly_gpu_risk_candidates_v1/dry_run_audit.json)의 SHA256은 `bccc573ea0f4f8fca9357d72bdc56f60788c85c31c2065d75f639322412bbf0f`이다. 이 시점에는 완전 관측 정상·초과 층의 게이트 통과와 부분결측 층의 실패만 확인했으며, 학습 모델의 시험 예측이나 점수는 보지 않았다.

| 고정 구성 | SHA256 |
| --- | --- |
| [후보·게이트 코드](../scripts/prepare_philly_gpu_risk_candidates.py) | `e89fd8a48c8491d02194540fc362a1d50f264f589a60e52e6c3dfd3fdc25d795` |
| [학습·기준선 코드](../scripts/philly_risk_models.py) | `27eee299b7b4a30844712200e85696719aea116699711e4620a12672dfed5606` |
| [지표 코드](../scripts/philly_risk_metrics.py) | `bc9bf2dfbd78e9e88405fe531d9163402c19309d2eaa05f0e994e1aaba81de72` |
| [채점 래퍼](../scripts/evaluate_philly_gpu_risk.py) | `0fad08527467fab39f03acae914c1c009a6c6a80852ab7701f06e068ea4f1c4f` |

Philly 관련 **합성 테스트 35개가 통과**했다. 래퍼는 명시적으로 전달된 dry-run 해시와 동결 문서·원본·정규화·후보 파일의 해시를 확인하고, 정규화 원본에서 후보와 정답을 재생성한다. 저장된 후보 특징·미래 관측성 행을 같은 키·값·순서로 대조한 뒤에만 관측된 정답을 학습에 쓴다. 대체 중앙값·표준화·모델 계수·Beta 확률표는 학습 기간에서만 맞추며 정규화 강도와 단순 기준선은 검증 기간의 Brier로 고른다. 시험에는 사전 게이트를 통과한 **완전 관측 정상·초과 두 층만** 채점하고, 과거 부분결측 성능은 계산하지 않는다.

실행 명령은 `python3 scripts/evaluate_philly_gpu_risk.py --expected-dry-run-sha256 bccc573ea0f4f8fca9357d72bdc56f60788c85c31c2065d75f639322412bbf0f`로 고정한다. 명시된 해시·소스 품질·코호트 게이트·후보 재생성 중 하나라도 어긋나면 점수 생성 전에 중단한다. 점수가 나온 뒤에는 이 문서의 해시나 동결 규약을 소급 수정하지 않는다.
