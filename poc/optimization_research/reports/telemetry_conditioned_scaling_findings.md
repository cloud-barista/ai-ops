# 실행 중 모니터링을 이용한 GPU 복제 성능 예측

## 질문과 데이터 경계

GPU 1개·CPU 2코어 **벤치마크 실행에서** 배치 지연, 요청/초, GPU·CPU 모니터링 요약을 알 때 **동일 호스트 GPU 2개·CPU 2코어의 정적 성능**을 더 정확하게 예측할 수 있는가? 이 질문은 현재 형상을 먼저 계측한 뒤 예상하는 Scale-out *이후의 정적 용량*에 관한 것이다. 원본 SQLite에는 VM 시작 시간, 부하의 시간 변화, 실제 Scale-out action과 그 결과가 없으므로 실제 오토스케일링 효과는 평가하지 않는다.

주 캠페인의 9개 모델·575개 작업 조건에서 모델 ID 하나를 통째로 빼고 학습했다. 학습에는 다른 모델의 GPU 1개/2개 쌍만 사용한다. 평가 대상 모델에서는 현재 GPU 1개·CPU 2코어 벤치마크의 성능과 그 실행의 `node_summary_json`만 입력한다. GPU 2개 실행의 값과 모니터링 정보는 점수 계산 때만 읽는다. 같은 방법을 이전에 보지 않은 novel-matrix 18개, space-filling 44개 조건에 적용했다. 외부 조건에서도 해당 GPU 1개 실행은 관측했다. 그러므로 이 실험은 **현재 형상의 계측 후 재형상화**를 가정하며, 배포 전 소수 프로브 방식과 측정 비용이 다르다.

기준선은 기존 앵커 조건부 ExtraTrees가 작업 특징과 GPU 1개 성능으로 GPU 2개 성능 비율을 예측한다. 새 후보는 같은 학습기에 GPU 1개 실행의 GPU 사용률 평균·최댓값, GPU 메모리 사용 최댓값, CPU 사용률 평균, 모니터링 샘플 수를 더했다. 모든 값은 현재 실행에서 수집되며, GPU 2개 실행의 계측값은 입력에 넣지 않는다. 샘플 수가 단순히 실행 시간을 대신하는지 확인하려고 GPU 계측만, 샘플 수 제외, 샘플 수만 쓰는 변형도 비교했다. 모델·분할·나머지 특징은 동일하다.

## 성능 예측 결과

아래는 GPU 2개·CPU 2코어 결과의 모델별 평균 절대 로그오차(MALE)를 다시 평균한 값이다. 9개 모델 각각이 같은 수의 의사결정 단위라고 보고 집계했다.

| 캠페인 | 기준 지연/처리량 | 전체 모니터링 지연/처리량 | 샘플 수 제외 지연/처리량 |
| --- | ---: | ---: | ---: |
| 주 캠페인, 575작업 | 0.244 / 0.112 | 0.248 / **0.098** | 0.248 / **0.096** |
| Novel matrix, 18작업 | 0.270 / 0.119 | 0.276 / 0.111 | 0.260 / 0.115 |
| Space filling, 44작업 | 0.298 / 0.171 | 0.280 / **0.123** | 0.287 / **0.125** |

샘플 수 제외 방식의 처리량 오차는 주 캠페인 9모델 중 8개, space-filling 9모델 중 8개에서 감소했다. 전체 모니터링 방식은 space-filling 9모델 모두에서 감소했다. 모델 단위 재표본추출의 **탐색적** 95% 구간에서 전체 방식의 기준선 대비 처리량 오차 차이는 주 캠페인 `−0.0144 [−0.0253, −0.0040]`, space-filling `−0.0479 [−0.0755, −0.0241]`이었다. Novel-matrix의 차이는 `−0.0079 [−0.0208, +0.0032]`로 불확실하다. 지연의 방향은 일관되지 않았다. `running_sample_count`만 더하면 주 캠페인의 처리량 오차는 0.112→0.111, space-filling은 0.171→0.166으로 대부분의 개선을 설명하지 못한다. 따라서 **현재 실행의 GPU/CPU 계측값이 GPU 2개 처리량 반응을 예측하는 데 도움이 될 가능성**은 보인다.

## 정적 Scale-out 판단 점검

실제 사용자 목표가 없어 훈련 donor 모델의 GPU 2개 지연·처리량 분위수 20~80%에서 각 5개씩 목표를 만들었다. 대상 모델의 실측을 목표 생성에 사용하지 않았다. 현재 GPU 1개가 목표를 못 맞출 때만, 예측상 GPU 2개가 두 목표를 맞출 것으로 보이면 Scale-out을 승인했다. 한 작업에 25개 목표를 적용한 **서로 상관된 가상 질의**이고 전환 비용·동적 수요는 없다.

| 캠페인 | 기준 성공/오승인 | 전체 모니터링 성공/오승인 | GPU 계측만 성공/오승인 | 실제 GPU 2개로 구제 가능한 질의 |
| --- | ---: | ---: | ---: | ---: |
| 주 캠페인 encoder 8모델 | 1,959 / 349 | 1,953 / 326 | 1,962 / 328 | 2,441 |
| Novel matrix encoder 8모델 | 69 / 5 | 71 / 5 | 74 / 6 | 99 |
| Space filling encoder 8모델 | 148 / 22 | 152 / 27 | 152 / 27 | 193 |

GPU 계측만 쓰면 주 캠페인의 잘못된 Scale-out 승인이 21건 적지만, space-filling에서는 5건 많다. Space-filling의 성공 4건 증가는 승인도 9건 더 한 결과다. 기준 지연 예측을 유지하고 모니터링 처리량만 쓰는 방식도 성공·오승인에서 일관된 이득이 없었다. 따라서 **처리량 예측 오차 개선은 정적 Scale-out 결정 개선으로 아직 이어지지 않았다.** 두 제약의 결합, 보수성, 승인 건수를 맞춘 비교와 더 많은 독립 조건이 필요하다.

## 해석과 선행 경계

현재 실행의 모니터링을 이용해 복제 후 성능을 예측하는 방향은 실용적 연구 후보지만, 모니터링+학습기 결합 자체를 새 원리로 주장하지 않는다. [MLscale](https://research.ibm.com/publications/using-machine-learning-for-black-box-autoscaling)은 black-box 계측으로 scale 후 지표를 예측하는 선행연구다. GPU 사용률은 수행한 일의 양을 직접 나타내지 않아 단일 임계값으로 SLO를 해석하기 어렵다는 [Google Cloud의 GPU 추론 운영 문서](https://docs.cloud.google.com/kubernetes-engine/docs/best-practices/machine-learning/inference/autoscaling)와도 일치하게, 여기서는 사용률만으로 action을 결정하지 않았다. 기존 결과를 여러 번 본 뒤 모니터링 특징군을 탐색했으므로 위 구간과 차이는 독립 검증이 아니다.

GPU 2개는 동일 호스트의 두 GPU replica이며 새 VM 두 대를 뜻하지 않는다. `latency_p95_ms`는 온라인 요청 p95가 아닌 배치 p95다. 수집기의 합성 SLO도 실제 사용자 약속이 아니다. 현재 데이터만으로 GPU/VM 생성, 전환 지연·비용, 부하 급변, Scale-in의 안정성을 주장할 수 없다.

재현: `OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/telemetry_conditioned_scaling.py`, 이어서 `python scripts/evaluate_telemetry_scaling_decision.py`. [학습 코드](../scripts/telemetry_conditioned_scaling.py), [판단 평가 코드](../scripts/evaluate_telemetry_scaling_decision.py), [성능 예측 산출물](../artifacts/telemetry_conditioned_scaling_v1/metrics.json), [정적 판단 산출물](../artifacts/telemetry_scaling_decision_v1/metrics.json), [누수 방지 테스트](../tests/test_telemetry_conditioned_scaling.py), [목표 생성 테스트](../tests/test_telemetry_scaling_decision.py).
