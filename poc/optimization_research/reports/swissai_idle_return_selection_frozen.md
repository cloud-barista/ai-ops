# SwissAI 유휴 모델 재도착 위험: 시험 전 선택 동결

**상태: FROZEN — 2026-09-28 14:37 UTC.** 이 기록은 2025-07-16~08-16 시험 확률·성능을 계산하기 전에 작성했다. 기존 SwissAI 서비스와 같은 시험 기간은 다른 연구에 사용됐고 5분 유휴 기준은 이 자료의 지지 수를 보고 정했으므로, 이번 결과는 [탐색 규약](swissai_idle_return_protocol.md)의 후향적 시험이다.

| 대상 | SHA256 |
| --- | --- |
| 평가 규약 | `fde797fe622163b796565ebbb97a88656ae1c1a4e1a77f8035c370037cc9e415` |
| [점수 전 자료 감사](../artifacts/swissai_idle_return_v1/dry_run.json) | `7444ee2a2e745fe64264d1c7ba2187a25e606e0062854e55e536682d70733143` |
| [학습·검증 선택 명세](../artifacts/swissai_idle_return_v1/model_spec.json) | `f0211ebaeeb8f45260e45be5625be7472217f7891e447985762893fb2a5ca719` |
| 선택된 모델 (`../artifacts/swissai_idle_return_v1/selected_model.joblib`) | `385201d2ba936f259847de7a52d8cdc57ec6a9109bed952bbf0151b9d46d59ae` |
| [선택 동결 manifest](../artifacts/swissai_idle_return_v1/selection_manifest.json) | **`0f13221e2b662040b9c7da157bc1ab24cdbed9094ed28956a7df8bbcfcf86feb`** |
| `evaluate_swissai_idle_return.py` | `51707577c35ea9f4c1a810fb5dc5fe43313cb9ee634fe08f4e8bb7fe2a395966` |
| 원본 `trace.jsonl` | `79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7` |
| 정렬 `requests_sorted.parquet` | `d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b` |

학습은 2,237건(양성 846), 검증은 780건(양성 335)이며 시험의 적격성은 845건(양성 276)으로 확인됐다. 전체 연구 테스트 366개가 통과했다. 시험 정답의 **건수**와 주별 지지 수는 점수 전 게이트로만 확인했고, 시험 확률·Brier는 아직 계산하지 않았다.

검증에서 로지스틱 회귀 Brier `0.2388408635`가 고정 후보 부스팅 `0.2606742960`보다 낮아 **로지스틱**을 선택했다. 모델별 경험적 재도착 기준선의 수축 강도는 `κ=100`(검증 Brier `0.2402616969`), Poisson 요청률 창은 **120분**(검증 Brier `0.3789664041`)으로 각각 검증에서 선택했다. 검증의 학습기 대 가장 강한 단순 기준선 차이는 작으며 시험 성과를 암시하지 않는다. 이 선택과 특징·계수는 manifest와 모델 명세가 지정하는 파일에서 변경하지 않는다.

시험 성공은 주 Brier가 **두** 선택 기준선 각각보다 상대 5% 이상 낮고, 네 완전 시험 주 중 최소 3주에서 두 기준선 모두보다 낮아야 한다. 실패하면 학습기를 운영용 `SCALE_IN` 승인기에 연결하지 않는다. 성공해도 의미는 같은 SwissAI 기록의 유휴 뒤 재도착 확률 예측까지이며 실제 축소 명령의 효과는 아니다.
