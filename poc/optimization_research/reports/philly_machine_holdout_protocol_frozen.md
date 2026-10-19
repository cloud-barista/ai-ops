# Philly GPU 위험 예측: 미관측 기계 홀드아웃 동결 기록

**상태: FROZEN — 2026-09-28 13:52 UTC.** 새 기계 해시 구간 `[5,15)`의 GPU 값·정답·예측 점수를 열기 전에 이 규약과 기존 기계로만 만든 모델을 고정했다. 상세 모집단, 시간 창, 품질 게이트, 점수, 성공 기준은 아래 해시의 [규약 초안](philly_machine_holdout_protocol_draft.md)에 적힌 그대로 적용한다. 기존 21개 기계의 시험 결과를 이미 본 뒤 통합 모델을 선택한 **후속 확인 평가**다.

## 고정 입력과 구현

| 파일 또는 입력 | SHA256 |
| --- | --- |
| `philly_machine_holdout_protocol_draft.md` | `f7ecbea5ef6e9f727207aebf8c8e92bdd5713995265c0035344a7013f0cf08ec` |
| `model_spec.json` — 기존 기계만으로 적합한 통합 로지스틱과 Beta 확률표 | `861005a57cbf9f370a8f36682ca9004a3301af01f45e9c797949bf6319ad76a3` |
| `fetch_philly_gpu_holdout.py` — 신규 기계 수집기 | `8ceda7a4948262d4ec599aacfa52c31596e6b58244d6729e89418dc134e193e6` |
| `normalize_philly_gpu_sample.py` | `547d1fd9f48c71ef62e75ddbfae2b7f45811b80d95b681910a0f4d4f720c0a01` |
| `prepare_philly_machine_holdout.py` | `be18b93c61c69ea4f526b0f52602b8ca648ad7745dd9739c5c6a230ec3a1666f` |
| `evaluate_philly_machine_holdout.py` | `c394d8aecdaea796809633b6e8b50d8bd5e79d2f2d18ace9c09aeb6737cd92a4` |
| `fetch_philly_gpu_sample.py` — 원본 CSV 파서 | `f58d42c68f2db651e8cc157938a3f029d54758afbc6551373f89eac7af0ce0ea` |
| `prepare_philly_gpu_risk_candidates.py` — 원래 후보·창 정의 | `e89fd8a48c8491d02194540fc362a1d50f264f589a60e52e6c3dfd3fdc25d795` |
| `philly_risk_models.py` | `27eee299b7b4a30844712200e85696719aea116699711e4620a12672dfed5606` |
| `philly_risk_metrics.py` | `bc9bf2dfbd78e9e88405fe531d9163402c19309d2eaa05f0e994e1aaba81de72` |
| `evaluate_philly_gpu_risk.py` — 기존 평가기 | `0fad08527467fab39f03acae914c1c009a6c6a80852ab7701f06e068ea4f1c4f` |
| 기존 정규화 Parquet | `15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58` |
| 기존 `metrics.json` | `cb3080d46c39c748a894065ab59c93df167fcb182c2bddb75e2205a816ddc4d2` |
| 기존 `dry_run_audit.json` | `bccc573ea0f4f8fca9357d72bdc56f60788c85c31c2065d75f639322412bbf0f` |
| 기존 `test_predictions.parquet` | `04ce609b36c468cfbec021c3a4900a3a08cc818b9f6c3cf95a0efc8f58bd5d4f` |
| Philly 원본 압축 아카이브, 1,055,988,361바이트 | `2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c` |

## 원본 기계만으로 확인한 모델

- 기존 학습 832,496행, 검증 292,580행으로 학습·선택했고 통합 로지스틱의 `C=10`, 기준선의 `state_recent` Beta 표를 고정했다. 새 기계의 10–11월 자료로 이 모수나 기준선을 다시 선택하지 않는다.
- 기존 시험의 완전 과거·현재 80% 미만층 217,750행을 같은 키에서 재생해 통합 모델 확률 최대 오차 `1.11e-16`, Beta 확률 최대 오차 `0`을 확인했다. 고정 모델의 나머지 모수와 특징 순서는 위 `model_spec.json`에 있다.
- 수집·준비·평가의 합성 테스트 32개와 전체 연구 테스트 356개가 통과했다. 이 확인은 새 기계 성능에 대한 증거가 아니다.

13:50 UTC의 최초 파일을 새 데이터를 수집하기 전 감사했다. 수집 감사에 기록된 동결 해시 3개를 준비·채점 단계에서 다시 대조하도록 `prepare_philly_machine_holdout.py`만 보강하고, 13:52 UTC에 위 코드 해시를 갱신했다. 모집단·모델·게이트·성공 기준은 바꾸지 않았으며, 이 개정 전에는 신규 기계 원본을 열거나 점수를 계산하지 않았다.

## 이후 실행과 해석

1. 원본 전체를 스트리밍 검사해 원시 ID의 SHA256 버킷 `5 ≤ r < 15`인 기계의 모든 GPU 분 행만 보존한다. 원본 해시·tar 구성·행 감사가 실패하면 새 표본을 채점하지 않는다.
2. 정규화 뒤, 모델 확률을 계산하기 전에 계측 품질·폭 불일치·정답 관측률·기계·날짜·두 달력 반구간의 계급 지지 수를 `dry_run_audit.json`에 저장하고 해시를 별도로 기록한다. 초안의 게이트를 완화하지 않는다.
3. 게이트가 통과하면 기존 모델 명세의 확률을 **12월 1–15일의 신규 기계**에 한 번 적용하고 초안의 Brier·기계 재표집·AP·일별 상위 5% 경보 recall을 산출한다. 실패한 층은 점수와 성공 판정을 보류한다.

이 시험은 **동일 Philly 클러스터와 동일 시기**에서 원시 `machineId`가 겹치지 않는 관측으로의 전이를 평가한다. 사용자 SLO, VM 증설 효과, `SCALE_OUT/IN` 판단의 인과적 이득을 검증하지 않는다. 새 홀드아웃을 보고 모델을 수정한다면 그 자료는 탐색용으로 재분류한다.
