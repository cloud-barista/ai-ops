# 계열 공동 제외에서 중심화 잔차 공분산 재검증

앞선 [계열 공동 제외 실험](family_holdout_transfer_findings.md)은 donor 잔차의 비중심 2차 모멘트를 사용했다. 여기서는 donor별 안쪽 모델 제외 잔차의 **평균을 먼저 제거한 공분산**을 사용하고, 새 모델의 사전 잔차 평균은 0으로 뒀다. 공분산과 부드러운 커널의 혼합 비율은 기존과 같은 50%다. 같은 절대 성능 모델·자원 반응 모델, 같은 학습 donor, 평가 모델마다 같은 GPU 1개·CPU 2코어 앵커 8개와 같은 실제 GPU-wall 비용을 썼다.

| 공동 제외 대상 | 방법 | 지연 MALE | 요청/초 MALE | SLO 성공 | 위반 | 평균 프로브 GPU-wall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| BERT 4종 | **중심화 공분산·평균 0** | 0.3395 | 0.1391 | 174/180 | 6 | 3.97초 |
| BERT 4종 | 비중심 2차 모멘트 | 0.3386 | **0.1389** | 174/180 | 6 | 3.97초 |
| BERT 4종 | 부드러운 커널 | **0.3392** | 0.1506 | 172/180 | 8 | 3.97초 |
| BERT+DistilBERT 5종 | **중심화 공분산·평균 0** | 0.3853 | 0.1243 | 208/217 | 12 | 4.20초 |
| BERT+DistilBERT 5종 | 비중심 2차 모멘트 | 0.3868 | **0.1238** | **209/217** | 12 | 4.20초 |
| BERT+DistilBERT 5종 | 부드러운 커널 | **0.3695** | 0.1472 | **209/217** | 13 | 4.20초 |

**결론은 바뀌지 않는다.** BERT 네 종을 함께 제외하면 중심화 공분산과 커널의 지연 오차는 거의 같다(0.3395 대 0.3392). DistilBERT까지 제외하면 중심화 공분산은 커널보다 지연 오차가 **0.0159 크고**, 다섯 평가 모델 모두에서 그 방향이 같다. 반면 처리량 오차는 중심화 공분산이 커널보다 **0.0229 작고**, 다섯 모델 모두에서 그 방향이 같다. 다섯 종 분할에서 중심화 공분산의 SLO 성공은 커널 및 비중심 방식보다 한 건 적다.

중심화 공분산에 donor 평균을 별도로 더한 변형도 계산했다. BERT 4종 분할에서 지연/처리량 MALE은 0.3363/0.1429, SLO 성공 174/180·위반 10건이었다. 다섯 종 분할에서는 0.3974/0.1367, 성공 208/217·위반 13건이었다. 평균 추가가 두 지표와 선택 결과를 동시에 개선하지는 않았다.

이 평가는 고정 SQLite의 모델을 새 방식으로 나눈 탐색 실험이다. 합성 SLO, 단일 호스트·GPU, 근연 encoder donor가 남는다는 한계가 있다. 비중심 prior의 평균 성분을 제거해도 지연·처리량 상충이 유지되므로, 현재 자료로 중심화 공분산을 **일반화 성능이 입증된 신규 알고리즘**으로 주장할 수 없다.

## 재현

- `OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_centered.py`
- `OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_centered.py --include-distilbert`
- 코드: `scripts/family_holdout_centered.py`; 기존 분해·예측 함수: `scripts/covariance_bias_ablation.py`
- 산출물: `artifacts/family_holdout_centered_bert4_v1/`, `artifacts/family_holdout_centered_bert5_v1/`
- 검증: `tests/test_family_holdout_centered.py` 2개 통과. 비중심·커널 행별 예측이 앞선 계열 제외 실험의 행별 예측과 일치하며, 프로브 ID·비용도 일치한다.

각 산출물의 `metrics.json`에 고정 스냅샷·실행 스크립트 SHA-256과 분할·집계가 기록되어 있다.
