# BurstGPT 출력 토큰 실험: 시험 전 선택 기록

2026-09-28. 시험 결과를 읽기 전에 검증 산출물과 선택을 확인했다.

- 선택 규칙: 상대일 303–314의 일별 동일 가중 p90 pinball 손실 최소.
- 선택된 방법: `hgb_base`.
- 검증 대상: 603,725 요청. 선택 방법의 일별 동일 가중 손실은 28.1346 토큰, 포괄률은 86.01%다.
- [`selection_manifest.json`](../artifacts/burstgpt_output_tokens_v1/selection_manifest.json) SHA256: `e613ce78feec71931fadbf36a2c2b399cc1d4fcbf5af931d67c4f21a42a1f35d`.
- [사전 규약](burstgpt_output_tokens_protocol.md) SHA256: `e8e2c1d6e9bd1070998c5f7ddfe163da9012167638b6bf150d7dbb8d09ea14e6`.
- [평가 코드](../scripts/evaluate_burstgpt_output_tokens.py) SHA256: `5475c633b277a750df5149750e2431dd4c1cf13731f9ee25b92e94a845536482`.

검증 포괄률이 90%보다 낮으므로 선택 방법은 이 시점에서 안전 상한으로 채택하지 않는다. 시험 자료는 검증 선택이나 하이퍼파라미터 변경에 사용하지 않는다.
