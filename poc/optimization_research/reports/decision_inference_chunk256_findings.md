# 정적 GPU 후보 묶음 예측: 64→256격자 결과

## 결론

**판정: `GO_CHUNK256`.** 고정 저장 ExtraTrees의 오프라인 다중 질의에서 [사전 규약](decision_inference_chunk256_protocol.md)의 출력 동등성·호출 수·시간·메모리 기준을 모두 통과했다. [v6 연구 경로](../scripts/assess_gpu_shape_microbatch_v6.py)는 최대 256격자를 한 번에 예측한다. 이전 v4/v5 코드는 과거 결과 재현을 위해 그대로 보존한다.

| 요청 형태 | 예측 호출 64→256 | 벽시계 중앙값 64→256 | 7쌍 중앙 속도비 | peak RSS 64→256 |
| --- | ---: | ---: | ---: | ---: |
| 서로 다른 575격자 | **9→3** | **747.305→528.949 ms** | **1.518배** | 441.930→442.055 MiB |
| 45격자×25 SLO, 후보 복사 | 1→1 | 161.889→172.900 ms | 0.953배 | 443.453→443.676 MiB |
| 45격자×25 SLO, 후보 공유 | 1→1 | 116.221→111.346 ms | 0.995배 | 430.906→432.992 MiB |

속도비는 각 짝의 `64 시간/256 시간`을 계산한 뒤 7개 중앙값이다. 두 시간 중앙값의 비와 같을 필요는 없다. 서로 다른 575격자는 7쌍 모두 **1.403~1.552배** 빨랐다. 공유·복사 입력은 양쪽 모두 한 번만 예측하므로 속도 차이를 이득으로 해석하지 않는다. 세 형태 모두 정규 JSON 전체가 초기 및 7쌍에서 정확히 같았고 RSS 증가가 사전 20 MiB 한도보다 작았다. 이전 64격자 보고서와 이번 절대 시간은 실행 시점의 CPU 상태가 달라 직접 비교하지 않는다.

## 구현·검증

기존 v4는 `max_grids≤64` 입력 검증을 하므로 [벤치마크](../scripts/benchmark_gpu_shape_chunk256.py)는 **호출 동안에만** 이 상수를 256으로 완화해 동일한 v4 예측·선택 경로를 비교했다. 통과 뒤 새 v6를 별도로 만들었고, 원본 v4/v5 또는 기본 평가기의 `MAX_CACHED_GRIDS=64`는 수정하지 않았다. v6는 기존 후보 검증, 복사 격자 재사용, 실패 시 원래 8후보 격자별 재시도를 유지한다.

실제 고정 모델을 v6로 다시 호출해 공유 1,125질의·복사 1,125질의·고유 575질의에서 v5와 JSON 전체가 같은 것을 확인했다. v6 예측 호출은 각각 **1·1·3회**였다. [경계 테스트](../tests/test_assess_gpu_shape_microbatch_v6.py)는 65고유 격자의 기존 2→1회 호출, 큰 예측 호출 실패 시 1+65회 재시도, 복사/무효/미지원 입력의 정확한 결과, v5의 64 상한 보존을 검사한다. v2~v6 관련 **21개 테스트**가 통과했다. 시간·출력·RSS와 입력·코드 SHA는 [결과 JSON](../artifacts/gpu_shape_chunk256_v1/results.json)에 있다.

재현:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python poc/optimization_research/scripts/benchmark_gpu_shape_chunk256.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python -m unittest discover -s poc/optimization_research/tests -p 'test_assess_gpu_shape_microbatch_v*.py' -v
```

이 결과는 **같은 호스트의 저장 배치 성능 모델을 warm 상태에서 CPU로 평가한 시간**이다. 모델 로드·JSON 전송·온라인 사용자 지연·VM 생성/배포·실제 `SCALE_OUT/IN` 효과는 측정하지 않았다. 256은 시험한 두 크기 중 이 세 입력에서 채택한 값이며 다른 모델이나 장비의 최적값이라는 뜻이 아니다.
