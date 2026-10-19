# 정적 GPU 후보 묶음 예측: 최대 64격자 결과

## 결론

**판정: `GO_CHUNK64` — 같은 저장 모델의 오프라인 다중 질의 경로에서 최대 64격자를 한 번에 예측한다.** [점수 전 규약](decision_inference_chunk64_protocol.md)의 출력 동등성·시간·메모리 기준을 모두 통과했다. 기존 [v4 평가기](../scripts/assess_gpu_shape_microbatch_v4.py)의 검증·SLO 선택·복사 격자 재사용 코드는 그대로 두고, [v5 호출 경로](../scripts/assess_gpu_shape_microbatch_v5.py)는 기본 `max_grids`만 8에서 **64**로 설정한다. 저장 모델 재학습이나 후보 추천 규칙 변경은 없다.

| 입력 | 8→64의 예측 호출 | 벽시계 중앙값 8→64 | 7쌍 중앙 속도비 | peak RSS 8→64 |
| --- | ---: | ---: | ---: | ---: |
| 45격자×25 SLO, 같은 후보 객체 공유 | 6→1 | 387.147→158.901 ms | **2.354배** | 438.359→438.770 MiB |
| 45격자×25 SLO, 후보 객체마다 복사 | 6→1 | 434.306→225.578 ms | **2.007배** | 443.648→444.039 MiB |
| 서로 다른 575격자 | 72→9 | 3,565.453→950.536 ms | **3.753배** | 441.285→442.309 MiB |

속도비는 각 쌍의 `8의 시간 / 64의 시간`을 구한 뒤 7개 중앙값이다. 두 경로의 시간 중앙값끼리 나눈 값과 같을 필요는 없다. 이전 v4 보고서와 이번 실험의 절대 시간은 실행 시점의 CPU 상태가 달라 직접 비교하지 않는다. 이번 **같은 실행의 짝지은 비교**에서는 복사 입력의 속도비 범위가 1.650–2.219배, 고유 격자는 3.247–4.005배였다. CPU 시간도 벽시계 시간과 거의 같았다.

주 시험의 실행 순서는 7쌍 중 8격자 먼저 5쌍, 64격자 먼저 2쌍이었다. [사후 교차 순서 확인](../artifacts/gpu_shape_chunk64_v1/balanced_order_posthoc.json)은 각 입력에서 **2쌍씩 순서를 바꾼 4쌍**을 새로 실행했고 출력 JSON 동등성을 다시 확인했다. 복사·공유·고유 격자의 중앙 속도비는 각각 **1.823·2.285·3.433배**, 가장 작은 쌍별 속도비도 각각 **1.531·1.232·3.339배**였다. 이 확인은 주 시험 이후의 탐색 점검이며 별도 장비·입력 검증은 아니다.

## 출력과 실패 경계

세 입력 각각에서 7번의 셔플 순서 모두 **결과 JSON 전체가 정확히 같았다.** 공유/복사 입력의 정규 결과 SHA-256은 `87834d2c56f7b1924adecbaaf3bfa40e4a4821cf7e8f0f4a07943abb5fbcc924`, 고유 격자는 `1054a72560b9c6ba06deece136633e92fb9eeecd3d10a0915edb8812bd141e99`로 기존 v4의 기록과도 같다. 실제 계산에는 저장 ExtraTrees의 같은 8후보 예측과 같은 수집기 생성 SLO를 썼다. peak RSS 증가의 최대는 고유 격자의 **1.024 MiB**로 사전 20 MiB 한도 안이다. [전체 원시 시간·CPU 시간·메모리·게이트](../artifacts/gpu_shape_chunk64_v1/results.json)에 남겼다.

기존 v4의 `_predict_grids`는 큰 결합 예측이 실패하면 각 원래 8후보 격자별로 다시 예측한다. [v5 경계 테스트](../tests/test_assess_gpu_shape_microbatch_v5.py)는 9개 격자에서 기본 호출 수가 **2→1**로 줄고 출력 전체가 같음을 확인한다. 또 8개 후보보다 큰 예측 호출이 `MemoryError`를 내는 모의 예측기에서는 큰 호출 1회 뒤 각 격자 9회 재시도해 기존 결과를 얻는다. 따라서 고정 모델 이외의 예측기가 큰 묶음을 받지 못할 때도 기존 동작 경계가 유지된다.

## 재현과 한계

[벤치마크 코드](../scripts/benchmark_gpu_shape_chunk64.py)는 앞선 v4의 핀 고정 모델·원본·분할·평가기 SHA를 확인한다. 모델/자료 적재와 첫 완전 호출은 시간에서 제외했고 BLAS/OMP/MKL은 한 worker로 고정했다. 각 입력의 peak RSS는 별도 프로세스에서 측정했다. [교차 순서 확인 코드](../scripts/verify_gpu_shape_chunk64_order.py)도 보존했다. 실행 명령은 다음과 같다.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python poc/optimization_research/scripts/benchmark_gpu_shape_chunk64.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python -m unittest discover -s poc/optimization_research/tests -p 'test_assess_gpu_shape_microbatch_v5.py' -v
```

이 결과는 같은 호스트·알려진 모델의 **저장된 배치 성능 모델을 오프라인으로 평가하는 CPU 시간**이다. 64는 이 세 입력에서 시험한 배치 크기이며 모든 모델·장비의 최적값이라는 뜻이 아니다. 후보는 한 호스트의 GPU 복제 프로세스와 CPU affinity이고, 생성 SLO는 실제 사용자 목표가 아니다. 이 최적화가 VM 배포·`SCALE_OUT/IN`의 효과나 온라인 p95 지연을 개선했다고 해석하지 않는다.
