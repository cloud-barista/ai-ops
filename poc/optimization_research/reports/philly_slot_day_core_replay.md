# Philly GPU 슬롯 위험 보정: 플랫폼 중립 연구 모듈 분리

2026-09-29. [새 기계 검증에서 통과한 전날 슬롯 보정](philly_slot_day_calibration_holdout_findings.md)을 평가 전용 스크립트와 별도로 [순수 계산 모듈](../policies/philly_slot_day.py)의 `calibrate_day()`로 옮겼다. 입력은 **한 UTC일의 현재 정상 GPU 슬롯별 기본 위험 확률**과 **바로 전날의 완료·가용한 피드백**이다. 출력은 기계별 보정 확률, 기계+슬롯 보정 확률, 적용된 절편, 전날 사용 행수다. VM ID·Manifest·`DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 행동을 만들지 않는다.

## 입력과 시점 계약

`targets`에는 `machine_hash`, `gpu_index`, `decision_utc_s`, `p0`만 허용한다. **당일 정답 `y` 열을 포함하면 거부**한다. `previous_day_feedback`에는 같은 네 필드와 이진 `y`, `label_available_at_utc_s`를 요구한다. 모든 피드백 판단은 대상 UTC일의 바로 전날에 있어야 하며, 정답 가용 시각이 판단+30분 이상이어야 한다. 시간·확률 범위와 중복 판단을 검사한다.

보정기는 전날 기계×슬롯×시간당 **가장 이른 적격 판단 한 건**을 남긴 다음, 정답 30분 창과 전달이 목표일 00:00 UTC까지 완료된 행만 사용한다. 기계별 벌점 20·이동 한계 ±0.5의 정확한 로그오즈 절편을 적용하고, 그 뒤 슬롯별 잔차를 같은 벌점·한계로 수축한다. 해당 기계 또는 슬롯의 전날 만기 행이 없으면 그 부분의 보정값은 0이다. 이 계산은 기존 시험 방법과 같다.

모듈은 `p0`와 피드백의 **출처를 스스로 확인하지 못한다**. 호출 Adapter는 원래 Philly 위험 모델이 지원하는 완전 과거 세 창·현재 사용률 80% 미만 조건, 기계·슬롯 식별자, 관측 정답과 실제 전달 시각을 별도로 검증해야 한다. 공개 Philly 원본에는 전달 시각이 없어, 아래 재생에서는 `label_available_at_utc_s=decision_utc_s+1800`이라는 **즉시 가용 가정**을 명시해 사용했다. 실제 연동에서 늦게 도착한 결과는 제외된다.

## 기존 단회 시험의 전 행 재생

[독립 재생 코드](../scripts/verify_philly_slot_day_core.py)는 원래 동결 시험 예측 (`../artifacts/philly_slot_day_holdout_v1/test_predictions.parquet`)의 SHA-256을 먼저 확인했다. 15일·51기계·**812,729판단**을 날짜별로 재생해 기계별 확률과 슬롯별 확률의 최대 절대 차가 각각 **0**이었다. Brier도 기계별 **0.085786455625**, 슬롯별 **0.083532084270**으로 원래 점수와 일치했다. [검산 JSON](../artifacts/philly_slot_day_holdout_v1/core_replay_verification.json)은 원본 결과·예측 해시와 가용 시각 가정을 보존한다.

[계약 시험](../tests/test_philly_slot_day_core.py) 4개는 당일 정답 열 거부, 슬롯별 다른 결과, 자정에 늦은 피드백 제외, 바로 전날 범위·중복·시간별 첫 행 규칙을 확인했다. 실행 명령:

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m unittest discover -s tests -p 'test_philly_slot_day_core.py' -v
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/verify_philly_slot_day_core.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/verify_philly_slot_day_core_delays.py
```

[피드백 지연 민감도](philly_slot_feedback_delay_findings.md)의 추가 전달 지연 **0분·5분·1시간·6시간·12시간·24시간**도 새 모듈로 전 행 재생했다. [독립 지연 검산](../scripts/verify_philly_slot_day_core_delays.py)에서 각 지연의 기계별·슬롯별 **확률 배열 SHA-256이 기존 결과와 모두 일치**했고 Brier도 같았다. [검산 기록](../artifacts/philly_slot_day_holdout_v1/core_delay_replay_verification.json). 24시간 지연에서는 81만여 판단의 두 확률이 전부 원래 `p0`로 돌아왔다. 이는 이미 열람한 같은 자료의 재현성 검사이며 실제 전달 지연 분포를 측정한 결과가 아니다.

이번 변경은 **검증된 연구 알고리즘을 재사용 가능한 계산 경계로 분리**한 것이다. 기존 시험의 독립성을 새로 늘리거나 다른 클러스터·AI 추론 서비스로 일반화를 입증하지 않는다. 위험 확률은 사용자 SLO 위반이나 VM 증감 조치의 효과 정답이 아니다. 이노그리드/ETRI 연동 시에는 실제 모니터링·전달 시각·식별자와 플랫폼 중립 입력 계약을 먼저 합의해야 한다.
