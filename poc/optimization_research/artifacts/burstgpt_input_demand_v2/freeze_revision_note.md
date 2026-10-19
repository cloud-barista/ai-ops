# BurstGPT 입력 수요 v2: 점수 전 동결 수정 기록

- 첫 manifest는 [`freeze_manifest_superseded_before_score_20260928T213102Z.json`](freeze_manifest_superseded_before_score_20260928T213102Z.json)으로 바이트를 그대로 보존했다. SHA-256은 `e45adb088e408be9ad29b92982ae2e9fce306ebea623a8f890d0ea64077aa8c5`다.
- 독립 사전 검토에서 검증 예측의 NaN/무한대가 산출물 기록 전에 거부되는지 합성 테스트로 확인하라는 요청을 받았다. 실제 코드는 첫 동결 이전부터 이 유한값 검사를 포함했지만 테스트가 없었다. 테스트 하나를 추가해 원래 v2 테스트 9건에서 10건이 됐다. 코드·규약·출처·모델 규칙과 검증 정답은 바꾸지 않았다.
- 첫 manifest 이후 v2의 실제 보정량, 학습·검증 점수, day 285–334 시험 목표는 계산하지 않았다. 새 활성 [`freeze_manifest.json`](freeze_manifest.json)의 해시를 독립 검토한 다음에만 검증 실행을 허용한다.
