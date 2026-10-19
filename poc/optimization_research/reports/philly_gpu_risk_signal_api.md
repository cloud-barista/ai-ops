# Philly GPU 고사용률 연구 신호 API

2026-09-28. [호출 함수와 CLI](../scripts/assess_philly_gpu_risk.py)는 [이미 동결한 통합 로지스틱·Beta 모델 명세](../artifacts/philly_machine_holdout_model_v1/model_spec.json)를 다시 학습하지 않고 읽어, GPU **한 슬롯**의 다음 30분 고사용률 사건 확률 두 개를 반환한다. 모델 명세 SHA-256은 `861005a57cbf9f370a8f36682ca9004a3301af01f45e9c797949bf6319ad76a3`이다. 명세가 고정한 정규화 원본 SHA-256과 후보 생성기·원래 모델·점수 코드의 SHA-256도 예측 전에 검사한다. 불일치하면 CLI는 오류 코드 2로 종료하고 확률을 출력하지 않는다.

입력은 [예시 요청](../examples/philly_gpu_risk_request.json)처럼 Philly 출처, 기계 해시, GPU 슬롯 번호, 판단 시각 `d`, 같은 슬롯의 완전한 5분 평균 창 세 개를 명시한다. 창 시작은 각각 `d−1200`, `d−900`, `d−600`초이며 최신 창은 `[d−600,d−300)`이다. 각 창은 유효한 5개 분, 0–100%의 유한한 평균값, 창 종료 이후이면서 `d` 이전인 `available_at`을 요구한다. 현재 평균이 80% 이상이거나 값·시각·슬롯·출처가 맞지 않으면 `ABSTAIN`한다. **`source_domain`, 기계·GPU 슬롯 식별, 각 창의 평균·유효 분 수·수신 시각은 모두 호출자 선언이다.** API는 원시 분별 계측과 실제 수신 기록을 확인하지 않으며, 해당 기계가 2017년 Philly 추적에 속하는지 또는 선언된 시각이 추적의 시간 범위에 있는지도 확인하지 못한다. 그래서 정상 확률과 `ABSTAIN` 모두 `input_observation_verified=false`를 명시한다. 모델·정규화 학습 원본의 해시 검사는 이 입력 관측의 진위 검증이 아니다.

모든 JSON 요청 결과의 `assessed_request_sha256`은 키 정렬·공백 없는 UTF-8 JSON으로 계산한 요청 해시다. [버전형 연구 증거 생성 경로](philly_gpu_risk_evidence_v0.1.0.md)는 이 값을 원 요청과 대조한 뒤 상태·확률·판단 시각을 별도 JSON으로 보존한다.

정답의 정의는 `[d,d+1800)`의 여섯 5분 창 중 한 창이라도 같은 GPU 슬롯의 평균 사용률이 **80% 이상**인 사건이다. 기존 평가는 미래 여섯 창이 전부 관측된 후보만 채점했다. 따라서 출력 확률은 미관측 미래나 다른 서비스까지 검증된 값이 아니다. [기계 분리 후속 평가](philly_machine_holdout_findings.md)에서 완전 관측·현재 80% 미만의 새 기계 37대, 741,597건에 대한 통합 모델 Brier는 `0.077783`, 선택 Beta 기준선은 `0.089879`였다. 이 API 작업으로 새 시험 점수를 계산하지 않았다.

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/assess_philly_gpu_risk.py --input examples/philly_gpu_risk_request.json
```

[예시 출력](../examples/philly_gpu_risk_example_result.json)의 로지스틱 확률은 `0.8156130235216689`, 선택 Beta 확률은 `0.841413078666235`다. [계약 테스트](../tests/test_assess_philly_gpu_risk.py)는 이 입력의 세 평균값을 저장된 holdout 후보와 대조하고, 두 확률을 저장된 holdout 예측 행과 **정확히** 비교한다. 또한 잘못된 출처·슬롯·시각·값·관측 완전성의 보류와 모델·원본·코드 해시 오류의 예측 전 중단을 검사한다.

결과의 `deployment_authority=false`가 경계다. 이는 2017년 Philly 추적의 관측 위험 신호이며 VM 형상, 현재 플랫폼의 실제 사용자 SLO, `SCALE_OUT` 명령 또는 그 효과를 뜻하지 않는다. 다른 사업자·시기·응용 환경의 일반화는 확인되지 않았다.
