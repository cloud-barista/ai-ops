# 기준 측정 한 번으로 새 AI 모델의 인프라 반응 예측하기

상태: **탐색적 학습·검증 완료. 예측 메커니즘의 효과는 관찰했지만, 학술적 독창성과 배포 판단의 우월성은 입증하지 못했다.**

## 무엇을 연구했나

새 AI 모델의 배포 이력이 없을 때 작은 기준 형상에서 한 번 실행한다. 그 지연시간과 요청 처리량을 입력으로 사용해 GPU·CPU 후보별 **상대 성능 변화**를 예측한다. 지연시간과 처리량이 모두 목표를 만족할 가능성이 있을 때 두 번째 형상을 측정하고, 그 실측값으로 최종 가능 여부를 확인한다. 이는 경희대 담당 범위 중 **응용 특성을 반영한 인프라 형상 추천**의 성능 예측 부분이다. 요구사항 자연어 분석이나 시간에 따른 scale-out/in 판단까지 검증한 결과는 아니다.

한 입력 조건의 기준 형상은 GPU 1개·CPU affinity 2개이다. 후보 형상은 GPU 1/2개 × CPU affinity 2/4/8/16개, 총 8개다. 기준의 배치 p95 지연 `L_a`와 요청 처리량 `R_a`를 측정한 뒤 다른 형상 `c`의 로그 응답을 다음처럼 계산했다.

```text
log predicted_latency(c)   = log L_a + f_latency(static workload, c, log L_a, log R_a)
log predicted_requests/s(c) = log R_a + log GPU_count(c)
                              + f_throughput(static workload, c, log L_a, log R_a)
```

`f`는 이전 모델들의 기준 대비 차이를 두 출력으로 함께 학습한 ExtraTrees 회귀기다. 입력의 정적 특성에는 **기준 실행의 결과 payload에서 읽은 모델 파라미터 크기**, batch 크기, 입력 길이, 동시성, 작업 종류와 후보 형상이 포함된다. 미측정 후보의 결과 payload는 사용하지 않는다. `R = examples_per_second / batch_size`로 요청/초 단위를 맞췄다. 기준 형상 예측은 실측값과 정확히 일치시켰다.

GPU 수에 따른 이상적인 처리량 증가와 SLO 경계 가중치는 별도 ablation으로 평가했다. 최종적으로 효과가 입증된 요소는 **기준 실측을 회귀 입력으로 넣어 상대 반응을 조건화한 것**이다. 나머지 두 요소는 효과를 확인하지 못했다.

## 자료와 평가 경계

- 고정한 SQLite 추출본: `measurements.parquet` (`../data/audit_v1/measurements.parquet`), SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`.
- 주 평가: 같은 GPU 호스트에서 9개 AI 모델, 575개 입력 조건, 조건당 8개 형상, 총 4,600개 후보 측정. 모델을 하나씩 **통째로** 제외하고 다른 8개 모델로 학습했다. 제외 모델의 각 입력 조건에서는 기준 측정 1개만 예측 입력으로 공개했다.
- 추가 입력 조건: 같은 호스트의 novel-matrix 18개 조건과 space-filling 44개 조건. 각 모델을 제외한 주 평가 자료로만 학습했으므로 추가 조건의 결과는 학습에 사용하지 않았다. 다만 **9개 모델의 ID는 주 평가와 같다**.
- 점 예측 오차는 기준 외 7개 후보의 모델별 평균 절대 로그오차를 다시 9개 모델 평균으로 집계했다. 따라서 모델별 조건 수의 차이가 한 모델의 영향력을 바꾸지 않는다.
- throughput SLO는 요청/초이고 실측 `examples_per_second / batch_size`로 비교했다. SLO는 수집 코드가 생성한 값이다.

테스트 후보의 결과값을 바꿔도 예측이 변하지 않는 테스트를 추가했다. 분할과 후보 집합 검사는 [`test_conditional_response.py`](../tests/test_conditional_response.py), 이전 기준선 및 교차적합 실험 테스트와 함께 실행한다.

## 모델별 제외 평가 결과

아래 숫자는 **낮을수록 좋은 평균 절대 로그오차**다. 모두 같은 기준 측정 1개와 같은 모델 단위 제외 평가를 사용한다.

| 방법 | 지연시간 | 요청 처리량 | 두 번째 측정 100회에서 실측 통과 |
| --- | ---: | ---: | ---: |
| 정적 특징 ExtraTrees + 기준 오차 사후 보정 | 0.276 | 0.114 | 95 / 108 |
| 기준값 조건부 상대 반응 ExtraTrees | **0.219** | **0.090** | **98 / 108** |
| 기준값 조건부 상대 반응 RandomForest | 0.222 | 0.090 | **98 / 108** |
| 기준값 조건부 상대 반응 HistGradientBoosting | 0.214 | 0.096 | 97 / 108 |
| 기준값을 입력으로 쓰는 직접 절대값 ExtraTrees | **0.206** | 0.131 | 97 / 108 |
| 두 번째 형상의 성공 여부만 직접 분류 | 해당 없음 | 해당 없음 | 94 / 108 |

공정한 정적 특징+사후 보정 대비 조건부 상대 모델의 오차 감소는 지연시간 0.057, 요청 처리량 0.025다. 9개 모델 단위 쌍체 bootstrap 95% 구간은 각각 **[0.028, 0.092]**, **[0.006, 0.045]**다. 이 구간은 여러 방법을 살펴본 **탐색적 설명**이며 확증적 유의성 검정이 아니다. RandomForest와 차이는 지연시간 0.004, 처리량 0.000 수준으로, 방법 고유의 학습기 우월성을 보여주지 않는다. 직접 절대값 모델은 지연시간을 더 잘 예측했다.

같은 호스트의 추가 입력 조건에서 조건부 ExtraTrees / 정적+보정의 오차는 novel-matrix에서 **0.254/0.119 대 0.326/0.155**, space-filling에서 **0.237/0.102 대 0.306/0.125**였다. 이는 새 입력 조건에서의 확인이지만, 새로운 GPU 호스트나 새로운 모델 집합에서의 재현은 아니다.

### 판단 및 측정 예산

주 평가 575개 조건 중 기준 형상은 249개에서 SLO를 만족했다. 기준이 실패한 326개 중 GPU 2개·CPU affinity 2개를 실측하면 추가로 성공하는 조건은 108개다. 이 데이터에서는 이 두 형상만으로 실측상 가능한 **357개 모두**를 찾을 수 있으므로 일반적인 8후보 최적화 문제라고 해석하면 안 된다.

기준 측정 575회에 두 번째 측정 100회를 추가하는 동일 예산에서 조건부 ExtraTrees와 RandomForest는 108개 중 98개를 찾아 총 **347/357개** 조건을 실측으로 확인했다. 정적+보정은 95개를 찾아 **344/357개**다. 직접 분류기는 94개다. 조건부 모델과 정적+보정의 3개 차이는 소수의 조건에 집중됐고, 모델 단위 재표본화 구간이 0을 포함한다. **측정 예산의 확실한 절감이나 결정 성능의 통계적 우위를 주장할 수 없다.** 100회 기준 조건부 모델의 관측 GPU-wall 시간 합계는 약 1,005초, 이전 단순 보정은 약 1,004초였다. 두 번째 실행을 마친 뒤 그 실측값으로 SLO 충족을 확인했으므로 이 오프라인 선택의 관측 위반은 0건이다. 다음 실행에서도 위반하지 않는다는 보장은 아니다.

이 수치와 **실측 없이 7개 후보 예측만으로 바로 배포하는 정책**의 결과를 구별해야 한다. 후자는 주 평가에서 조건부 모델이 346개 성공·6개 위반이었다. 이 정책은 실제 배포에 권장하지 않는다.

## 실패한 가설과 ablation

- 기준 측정값을 회귀 입력에서 뺀 동일 계열 모델은 **0.282/0.111**이었다. 조건부 입력이 지연·처리량 오차 감소에 기여했다.
- GPU 수 `log`를 처리량 기준으로 빼지 않은 버전은 **0.219/0.089**로 비슷했다. 물리 기준의 효과는 관찰되지 않았다.
- SLO 경계 부근 샘플에 가중치를 준 버전은 **0.218/0.090**으로 비슷했다. 경계 가중치의 효과는 관찰되지 않았다.
- 모델 내부 교차적합으로 기준 오차를 학습한 별도 보정법은 **0.257/0.185**, 두 번째 측정 100회에서 **91개**만 찾았다. [실패한 가설 기록](crossfit_anchor_calibration_findings.md)에 남기고 채택하지 않는다.

## 선행연구와 독창성의 경계

[Paragon](https://people.csail.mit.edu/delimitrou/papers/2013.asplos.paragon.pdf)과 [Quasar](https://people.csail.mit.edu/delimitrou/papers/2014.asplos.quasar.pdf)는 소수의 새 응용 측정으로 자원별 성능과 QoS를 다루며, [MaverIQ](https://ut-sysml.ece.utexas.edu/publications/prints/sc2025_liakopoulos.pdf)는 소수의 LLM 프로파일로 여러 배포 구성의 응답을 추정한다. 따라서 **'한 번 또는 적게 측정해서 다른 형상을 추천한다'는 발상 자체는 신규하지 않다.** 이번 구현은 경희대 AI-MCMP 데이터에서 기준의 지연·요청 처리량을 상대 반응 입력으로 사용하는 **구체적인 실험 방법**이다. 실제로 기존 정적 보정보다 예측은 나아졌으나, RandomForest로도 같은 효과가 나왔고 선택 효과는 작다. 현재 자료만으로 논문 수준의 고유 방법이나 범용 추천 성능을 주장하기 어렵다.

## 다음 연구 단계

1. 조건부 상대 반응 방법과 강한 기준선의 구현·하이퍼파라미터·선택 규칙을 **새 자료 수집 전에 고정**한다. 소수 측정·구성 전이와 관련된 Paragon, MaverIQ 계열 접근과의 차이도 평가 표에 명시한다.
2. 새로운 AI 모델·서로 다른 GPU/VM 호스트·실제 사용자 SLO·복수 실행을 수집한다. GPU 가격과 VM 실행 비용을 포함해야 형상 추천의 **자원 비용**을 판단할 수 있다.
3. 모델 전체와 호스트 전체를 분리한 블라인드 평가에서 두 타깃 오차뿐 아니라 **같은 측정 GPU 시간당 실측 SLO 충족·비용**을 비교한다. 조건부 측정 이후의 불확실성 보장은 별도 방법과 검증이 필요하다.
4. 요구사항 분석과 scale-out/in 연구는 응용 설명·사용자 요청·시간 순서가 있는 모니터링 자료가 확보된 뒤 별도 학습 문제로 정의한다. 현재 배치 벤치마크만으로 동적 스케일링 정책을 학습·검증할 수 없다.

## 재현 자료

- [처음 정한 평가 프로토콜](novel_method_protocol.md)
- [조건부 상대 반응 학습 코드](../scripts/conditional_response.py), [강한 기준선](../scripts/strong_conditional_baseline.py), [두 번째 측정 평가](../scripts/evaluate_novel_decision.py)
- [비교 그림](../artifacts/novel_method_summary_v1/comparison.png), [기계 판독 요약](../artifacts/novel_method_summary_v1/summary.json)
- [원본 예측·판단 지표](../artifacts/novel_decision_v1/metrics.json), 모델별 지표 (`../artifacts/novel_decision_v1/prediction_by_model.csv`)

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -q
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/novel_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/conditional_response.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/strong_conditional_baseline.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/probe_classifier_baseline.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/crossfit_anchor_calibration.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_novel_decision.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/summarize_novel_method.py
```

각 아티팩트의 `metrics.json`에 자료와 실행 코드 해시를 보관한다. 이 평가에는 운영용 Deployment Manifest 생성이나 실제 VM 생성·삭제가 포함되지 않는다.
