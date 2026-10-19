# Philly 유휴 재활성화 v3: 검증 결과 NO-GO

2026-09-28. [점수 전 규약](philly_idle_rebound_adaptive_v3_protocol.md)과 [검증 채점 해석](philly_idle_rebound_adaptive_v3_scoring_note.md)에 따라 개발군의 **검증 4,839건·재활성 229건**을 한 번 채점했다. 감쇠 계수의 두 단계 수축 추정기는 고정 학습 Beta보다 Brier가 **0.046015 → 0.045143**, 상대 **1.895% 감소**했지만, 사전 요구한 5%에 못 미쳤다. 가장 강한 적응 기준선 `decayed_machine_3d_20`의 **0.045054**보다도 0.197% 높다. 사전 채택 조건 **7개가 모두 실패**해 이 방법을 채택하지 않고 후행 평가도 진행하지 않는다.

| 동일한 4,839건의 방법 | Brier ↓ |
| --- | ---: |
| 고정 `frozen_train_beta` | 0.046015 |
| 누적 fleet `expanding_fleet_40` | 0.045947 |
| 3일 감쇠 fleet `decayed_fleet_3d_40` | 0.045693 |
| 7일 fleet `rolling_fleet_7d_40` | 0.045810 |
| 3일 감쇠 기계 `decayed_machine_3d_20` | **0.045054** |
| 누적 계층 `expanding_hierarchical_40_20` | 0.045253 |
| 제안 계층 감쇠 `candidate` | 0.045143 |

고정 Beta 대비 5% 감소 기준은 Brier **≤0.043714**이고, 최선 적응 기준선 대비 2% 감소 기준은 **≤0.044153**이다. 제안법은 둘 다 넘었다. 기계 동일 가중 Brier도 제안법 **0.085717**, 여섯 기준선 중 최선 **0.082370**으로 악화했다. 첫 고정 반구간 `[11-16,11-23)`에는 제안법 **0.048800**이 그 구간 최선 기준선 **0.049113**보다 낮았지만, 두 번째 `[11-23,12-01)`에는 **0.042122**가 최선 **0.041502**보다 높았다.

전체 검증 Brier로 한 번 고른 기준선은 `decayed_machine_3d_20`이다. 이에 대한 제안법의 짝지은 Brier 차(제안−기준선)는 **+0.00008855**다. 사전 지정한 2,000회 재표집(seed 43)의 95% 구간은 기계 단위(13기계) **[−0.000431,+0.000879]**, UTC 날짜 단위(15일) **[−0.000476,+0.000688]**로 둘 다 0을 포함한다. 각 방법의 위험 최하위 20%인 동일한 968건에서 실제 재활성은 제안법 **44건(4.55%)**, 선택 기준선 **24건(2.48%)**이었다. 따라서 재표집 상한과 저위험층 조건도 통과하지 못했다.

[점수 전 지원량 감사](philly_idle_rebound_adaptive_v3_support_findings.md)는 fleet 80.78%, fleet·local 동시 44.66%로 사전 자료 문턱을 통과했다. 위 실패는 지원량 부족으로 점수를 생략한 결과가 아니라, 그 문턱을 통과한 **탐색 검증 점수**다. 방법은 기존 검증 결과와 개발군 12월의 양성 **총수**를 이미 알고 설계했다. 12월의 행별 이번 정답·확률 및 `[5,15)` 다른 기계군의 이번 정답은 열지 않았다. 이 GPU 사용률 추적에는 실제 VM 축소 명령과 사용자 SLO가 없어 `SCALE_IN` 효과를 평가할 수 없다.

근거: [동결 검증 지표](../artifacts/philly_idle_rebound_adaptive_v3/validation_metrics.json) SHA256 `0247625d9fc68f4b1b58c9cecbb076a79e1c48a7cef2804f4f1940a8f7316aba`; 동일행 예측 (`../artifacts/philly_idle_rebound_adaptive_v3/validation_predictions.parquet`) SHA256 `4dbe00675678993b2471e232adb33b139baea9235420f94268e60c59a73290dd`; [검증 채점 허가 manifest](../artifacts/philly_idle_rebound_adaptive_v3/validation_score_authorization.json) SHA256 `c75845d6f28316a2c22d1080c98ebf17a7815522baceb729573cbd3a15184d06`. 원본 개발군 정규화 Parquet SHA256은 `15f1af4669acb9823d0e6d96291e799cf33f8a9c1813d4faf51b0b9eb618ad58`다. 이 보고서는 저장된 지표를 요약했으며 새 채점은 하지 않았다.
