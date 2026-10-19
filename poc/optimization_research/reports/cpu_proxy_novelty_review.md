# CPU 측정을 GPU 형상 예측의 사전 정보로 쓰는 가설: 선행연구와 검증 범위

## 판정

**CPU 실행 결과로 GPU 성능을 예측한다는 발상 자체는 새롭지 않다.** CPU 동적 프로파일로 GPU 가속 성능을 예측한 [Baldini 등, SBAC-PAD 2014](https://research.ibm.com/publications/predicting-gpu-performance-from-cpu-runs-using-machine-learning)와 CPU 코드 특성에서 GPU 실행 시간을 예측한 [XAPP, MICRO 2015](https://research.cs.wisc.edu/vertical/wiki/index.php/Pubs2/B2hd-micro15xapp)가 직접적인 선행연구다. 싼 관측과 비싼 관측을 결합하고 일부 GPU 실측으로 차이를 보정하는 수학적 구성도 [Kennedy–O'Hagan, Biometrika 2000](https://academic.oup.com/biomet/article-abstract/87/1/1/221217), [다중 충실도 비용 인식 최적화, 2023](https://doi.org/10.1016/j.cma.2023.115937)에서 오래 다뤘다.

다만 **CPU 코어 수별 응답을 새 AI 모델의 작업 서명으로 사용하고, 극소수의 GPU 측정으로 GPU 1/2개 × CPU 한도 형상의 지연·처리량을 보정하며, 두 제약을 만족하는 형상 선택의 오류를 줄이는 방식**은 구체적인 연구 가설이 될 수 있다. 여기서도 CPU 신호가 어떤 조건에서 GPU 예측 오차를 설명하는지, CPU 측정을 선택할지 GPU 측정을 선택할지 결정하는 규칙이 기존 방법과 무엇이 다른지를 제시해야 한다. 지금은 방법의 제안 단계이며, **효용도 학술적 신규성도 입증되지 않았다.**

## 현재 SQLite가 허용하는 실험

고정 스냅샷 `data/audit_v1/measurements.parquet`의 성공한 주 캠페인 `khu-hf-local-exhaustive`에는 encoder 8모델의 GPU 측정 4,096건(모델당 입력 조건 64개 × 자원 형상 8개)과 CPU 측정 256건(모델당 **동일 입력 조건 8개 × CPU 코어 한도 4개**)이 정확히 짝지어진다. GPU 데이터의 Qwen 모델 504건에는 같은 캠페인 CPU 관측이 없다. 별도 `novel-matrix`와 `space-filling`의 encoder 8모델은 각각 모델당 GPU 입력 조건 2개와 5개이지만 CPU는 **각각 1개 입력 조건**에만 있다. 이 수치는 Parquet에서 `eligible`, `workload_task=inference`, `campaign`, `device`, `workload_group`으로 다시 계산했다. [데이터 감사](../data/audit_v1/data_audit.md)

주 캠페인의 CPU 256건은 실행 wall time 중앙값이 **7.48초**, GPU 1개·CPU 2코어의 대응 후보 512건은 **0.52초**다. 따라서 CPU 측정을 무조건 ‘더 빠른 저충실도 측정’이라고 부를 수 없다. CPU가 GPU 사용량을 절약할 수는 있지만, 총 경과 시간·CPU 자원 가격까지 더 저렴하다는 근거는 없다. 비용은 GPU-wall초, CPU-core-wall초, 총 시간으로 따로 보고해야 한다. 또한 CPU 실행과 GPU 실행은 서로 다른 자원 형상의 실제 결과다. CPU 결과를 GPU 결과의 저충실도 대리값으로 쓸 수 있는지는 경험적으로 확인해야 하며, 다중 충실도 모델을 적용할 수 있다는 이유만으로 둘의 오차가 잘 상관한다고 가정할 수 없다.

현재 자료는 한 로컬 GPU 호스트의 배치 측정 위주이고 실제 VM 가격, 사용자 요청 p95 SLO, 시간에 따른 스케일 전환 결과가 없다. 따라서 여기서 가능한 것은 **정적 형상 성능 예측과 합성 SLO에 따른 오프라인 형상 선택**이다. 운영비 절감·실제 배포/스케일 정책 개선을 결론으로 낼 수 없다.

## 가장 가까운 선행연구

| 요소 | 선행연구가 이미 보인 것 | 남는 연구 질문 |
| --- | --- | --- |
| CPU 신호로 GPU 성능을 추론 | [Baldini 등, 2014](https://research.ibm.com/publications/predicting-gpu-performance-from-cpu-runs-using-machine-learning)는 CPU에서 얻은 동적 프로파일 특징으로 18개 병렬 벤치마크의 GPU 가속 정도를 예측했다. [XAPP, 2015](https://research.cs.wisc.edu/vertical/wiki/index.php/Pubs2/B2hd-micro15xapp)는 CPU 코드에서 GPU 실행 시간을 예측한다. | 기존 SQLite의 CPU **코어 응답 곡선**이 정적 모델 메타데이터와 GPU 소수 측정보다 추가 정보를 주는가? |
| CPU와 GPU의 짝지은 관측, GPU 실측 보정 | [Kennedy–O'Hagan, 2000](https://academic.oup.com/biomet/article-abstract/87/1/1/221217)은 저·고충실도 출력과 둘 사이의 discrepancy를 결합한다. [다중 충실도 비용 인식 BO, 2023](https://doi.org/10.1016/j.cma.2023.115937)는 신뢰할 수 없는 저충실도 관측을 판별한다. | CPU/GPU에서 병목이 바뀌는 지점에는 단순 비율이나 전역 선형 discrepancy가 실패할 수 있다. 실패를 어떻게 식별하고 전이를 줄일 것인가? |
| 적은 작업 측정으로 자원 형상 찾기 | [Ernest, NSDI 2016](https://www.usenix.org/conference/nsdi16/technical-sessions/presentation/venkataraman)는 소수 실험과 성능 모델로 자원 구성을 예측한다. [L2S, FSE 2018](https://www.cs.cmu.edu/~mvelezce/publications/JVKS-FSE18.pdf)는 이전 환경의 정보로 새 환경의 샘플을 고른다. [MaverIQ, SC 2025](https://github.com/UT-SysML/MaverIQ)는 LLM fingerprint와 분석 모델로 GPU 프로파일 비용을 줄인다. | CPU 코어 응답이 새 모델의 GPU 관측 위치와 자원 반응을 **실제로 더 잘** 고르는가? |
| 두 SLO와 측정 비용을 고려한 결정 | [Targeted Active Learning, 2021](https://arxiv.org/abs/2106.04193)는 최적 결정의 불확실성을 낮추는 질문을 고른다. [Multi-Fidelity Best-Arm Identification, NeurIPS 2022](https://papers.nips.cc/paper_files/paper/2022/hash/71c31ebf577ffdad5f4a74156daad518-Abstract-Conference.html)는 편향과 비용이 다른 관측을 골라 최선 후보를 찾는다. [FleetSieve, 2026](https://arxiv.org/abs/2608.19659)는 용량과 꼬리 지연을 함께 모델링해 SLO 형상 결정에 중요한 GPU 측정을 고른다. | CPU/GPU 측정을 함께 선택하는 특정 획득 규칙이 오승인·최소 형상 선택·비용을 함께 개선하는가? |
| CPU/GPU 이종 추론 자원 선택 | [HarmonyBatch, 2024](https://arxiv.org/abs/2405.05633)는 CPU·GPU 서버리스 추론의 성능·비용 모델로 SLO를 만족하는 자원 구성을 선택한다. [Vidur, MLSys 2024](https://proceedings.mlsys.org/paper_files/paper/2024/hash/b74a8de47d2b3c928360e0a011f48351-Abstract-Conference.html)는 제한된 GPU 프로파일과 예측으로 LLM 배포 형상을 탐색한다. | 본 자료에서 가능한 단일 호스트 replica 형상 외에 다른 VM·GPU 종류로 일반화할 자료가 있는가? 현재는 없다. |

논문의 적용 대상은 각각 다르다. 표는 **아이디어의 우선권과 기준선 선택**을 위한 것이며, 어느 논문이 현재 코드와 동일한 알고리즘이라는 주장은 아니다.

## 시험할 구체적 가설과 최소 비교

1. **검증 질문:** 한 모델의 CPU 코어 2/4/8/16별 로그 지연·처리량 곡선을 정규화한 서명 `z_cpu(x)`가, 동일 모델의 소수 GPU 측정과 정적 특징만으로 예측한 결과에 비해 *미관측 입력 조건과 GPU/CPU 형상*의 성능을 개선하는가? CPU가 없는 입력 조건은 추가 GPU 또는 CPU 측정 없이 값을 채운 것으로 취급하면 안 된다.
2. **방법 후보:** 기존 GPU 기준 예측기 `f(x,a)`를 고정하고, 훈련 모델에서만 배운 CPU→GPU 잔차 함수 `d(z_cpu,x,a)`를 더한다. 새 모델의 일부 GPU 측정으로 `d`를 보정하되, CPU/GPU 불일치가 큰 모델·조건에서는 내부검증으로 정한 신뢰도에 따라 보정을 줄이거나 abstain한다. `a`는 GPU replica 수와 CPU affinity 한도다. 이 구성은 선행 다중 충실도 모델의 응용이며, **새로운 핵심 기여로 주장하려면** 병목 변화 감지·보정·형상 결정 비용을 결합한 규칙에 별도의 검증 가능한 차이가 있어야 한다.
3. **필수 대조군:** 정적 특징만 사용, CPU 특징만 사용하는 지도학습, 기존 GPU-only 소수 앵커 전이, 짝지은 CPU/GPU에서 단순 비율/회귀 discrepancy, 표준 다중 충실도 GP 또는 동등한 유연한 모델, CPU 서명 순열 대조. CPU+GPU 후보를 0 GPU 측정과 동일 비용 비교하거나 CPU 실행 비용을 0으로 치는 비교는 피한다.
4. **분할:** 모델 전체 제외를 기본으로 하되 BERT 계열을 함께 제외하고, CPU가 있는 8개 주 입력 조건과 CPU가 없는 나머지 56개를 따로 평가한다. `novel-matrix`·`space-filling`은 사후 방법 선택에 이미 여러 번 사용됐으므로 **탐색 평가**로 표기한다. 새 호스트/모델/사용자 SLO의 독립 검증은 현재 불가하다.
5. **평가:** 배치 p95·처리량의 모델별 평균 절대 로그오차, 일부 형상만 가능한 합성 SLO에서 성공·잘못 승인·최소 GPU/CPU 형상 일치, GPU-wall초·CPU-core-wall초·총 시간의 Pareto 관계를 함께 보고한다. 한 모델의 여러 조건과 여러 합성 목표를 독립 표본처럼 세지 않는다. 모델/계열별 결과가 개선되지 않으면 평균만 좋은 방법을 채택하지 않는다.

**가장 먼저 할 작은 실험**은 주 캠페인에서 CPU가 있는 8개 조건만 대상으로, CPU 코어 응답 서명을 추가했을 때 GPU-only K=4/K=8 앵커 예측보다 모델 단위 제외 성능이 개선되는지 확인하는 것이다. 이 단계에서 정보량이 없으면 복잡한 선택 정책을 만들 이유가 없다. 정보량이 보이더라도 56개 CPU 미측정 조건으로의 확장과 실제 비용 우위는 별도 반증 시험이 필요하다.

## 노벨티를 주장하려면 필요한 추가 증거

현재의 **CPU proxy + sparse GPU calibration**은 이미 알려진 방법들의 조합이므로 이름을 붙이거나 통계적 회귀를 조금 바꾸는 것만으로 새 알고리즘이 되지 않는다. 연구 기여 후보는 (a) CPU/GPU 병목이 달라져 저충실도 관측이 오히려 해로울 때 이를 모델/조건 단위에서 식별하는 검증 가능한 규칙, (b) 해당 신뢰도와 두 SLO의 경계를 함께 이용해 다음 CPU/GPU 관측을 고르는 획득 규칙, (c) 같은 측정 비용의 강한 기준선 대비 계열·입력 분포 이동에서도 형상 오선택을 줄이는 독립 결과의 **조합**에 있다. (a)와 (b)도 각각 관련 선행 원리가 있으므로 기술적으로 정확한 차이를 수식·ablation·강한 기준선으로 밝혀야 한다. 현 SQLite에서는 (c)의 독립 결과를 입증할 자료가 없어 **학술적 노벨티 판정은 보류**한다.
