# 64격자 묶음 예측의 ExtraTrees worker 수: 정확 출력 게이트 실패

## 결론

**판정: `NO_GO_EXACT_OUTPUT`; 현재 1 worker를 유지한다.** [사전 비교 규약](decision_inference_workers64_protocol.md)에 따라 저장된 ExtraTrees 모델의 64격자 묶음에서 예측 worker를 1·2·4로 바꾸었다. 세 입력 모두 2·4 worker의 **정규 결과 JSON 해시가 1 worker와 달랐다.** 사전 규약은 출력 전체가 정확히 같아야 속도·메모리를 비교해 채택하도록 정했으므로, 다중 worker의 속도 점수는 내지 않았다. 저장 모델, v5 평가기, 추천 선택 규칙은 변경하지 않았다.

| 입력 | worker 2의 달라진 수치 필드 | worker 4의 달라진 수치 필드 | 최대 절대 수치 차이 | 상태·선택 형상 변경 |
| --- | ---: | ---: | ---: | ---: |
| 1,125개 복사 SLO 질의 | 1,410 | 6,123 | 2.27×10⁻¹² | **0·0** |
| 1,125개 공유 SLO 질의 | 2,007 | 6,785 | 2.73×10⁻¹² | **0·0** |
| 575개 고유 격자 | 535 | 3,626 | 1.32×10⁻¹¹ | **0·0** |

이 수치는 [저장된 한 번의 사전 출력 비교](../artifacts/gpu_shape_workers64_v1/results.json)다. 추가 읽기 전용 재생에서는 달라진 필드의 **개수가 달라졌다.** 다중 worker 결과의 마지막 자리 부동소수점 값은 실행 순서에 따라 변할 수 있음을 시사한다. 그 재생에서도 상태와 선택한 GPU·CPU 형상 변경은 관측되지 않았다. 현재 시험의 작은 차이를 자동 승인 기준에서 무시할 수는 없다. 다른 SLO 경계에서는 마지막 자리 차이가 합격 판정에 닿을 수도 있고, v4/v5는 결과 JSON의 정확한 동등성을 추론 최적화의 계약으로 사용한다.

설치된 scikit-learn 1.6.1의 `ForestRegressor.predict`는 각 트리 예측을 공유 배열에 **잠금 아래 더한 뒤** 트리 수로 나눈다. `_accumulate_prediction`은 병렬 작업이 완료될 때마다 합산한다. 위 수치 차이가 부동소수점 덧셈 순서에 민감한 현상이라는 해석은 이 로컬 구현과 관측값을 결합한 **추론**이다. 개별 사용자의 실제 지연·SLO·VM 배포 결과는 이 시험에 없다.

## 재현

[비교 코드](../scripts/benchmark_gpu_shape_workers64.py)는 기존 저장 모델·추출 자료·v4 벤치마크 해시를 확인하고, 예측 호출 안에서만 독립된 회귀기 복사본의 `n_jobs`를 바꾼다. 공유 bundle의 worker 값이 변하지 않았는지도 확인한다. [차이 집계 테스트](../tests/test_benchmark_gpu_shape_workers64.py)는 수치 변화와 선택 형상 변화를 별도로 세는 경계를 확인한다.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python poc/optimization_research/scripts/benchmark_gpu_shape_workers64.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python -m unittest discover -s poc/optimization_research/tests -p 'test_benchmark_gpu_shape_workers64.py' -v
```

현재 채택된 [64격자 기본값](decision_inference_chunk64_findings.md)은 1 worker로 예측한다. 이 결정은 한 호스트의 저장 모델과 정확한 JSON 동등성 요구에 대한 것이다. 다른 예측 모델에 worker 2·4가 언제나 부적합하다는 결론은 아니다.
