# 동일 작업의 SLO 변경 질의에서 예측 재사용 평가

2026-09-28. [사전 자료 적격성·평가 규약](scale_decision_feasibility.md)에 따라 **연구용 추론 비용 최적화 게이트를 통과**했다. 같은 작업의 GPU/CPU 8후보가 고정되고 지연·처리량 목표만 달라질 때, 후보 예측을 한 번 계산한 뒤 재사용해도 1,125개 고유 질의의 **전체 출력이 모두 같았다**. 다섯 번 반복한 5,625개 질의 쌍에서도 출력 불일치는 0건이었다. 실제 서비스의 SLO 변경 빈도나 배포·스케일 조치 효과는 측정하지 않았다.

## 방법과 결과

주 캠페인의 적격 GPU 측정에는 9모델·575작업군, 작업군마다 GPU replica 1/2개 × CPU affinity 2/4/8/16코어의 완전한 8후보가 있다. [작업군 목록](../artifacts/slo_reuse_v1/manifest.json)을 **결과 채점 전에** 모델별 `SHA256(workload_group)` 순서의 첫 5개, 총 45개로 고정했다. 각 작업군의 원본 **생성** SLO `(L₀, R₀)`에 5개 배수를 각각 곱해 25개 합성 목표를 만들었다. 이는 동일 작업의 반복 질의를 흉내 낸 것이며 실제 사용자 요청 1,125건이 아니다.

- 기본 경로: 매 SLO마다 [형상 평가기](../scripts/assess_gpu_shape.py)를 호출해 8후보를 다시 예측한다.
- 재사용 경로: 첫 SLO에서 계산한 **후보별 원시 예측만** 메모리에 보관한다. 이후 SLO에도 원래 형상 평가기의 요청 검사·SLO별 점수 계산·선택·응답 구성을 그대로 실행한다. 실험 코드는 단일 스레드에서만 예측 함수 호출을 일시적으로 가로챈다. 모델/원본 해시가 검증된 묶음, 원래 bundle·모델 객체, 후보 8개의 전체 순서와 필드, 지원 규칙이 바뀌면 캐시를 쓰지 않는다. 이것은 운영 서비스용 캐시 구현이 아니다.

두 경로를 같은 프로세스에서 저장 모델을 예열한 뒤 순서를 섞어 모델별 45작업군을 **5회 반복**했다. 모델 로드와 JSON/API 왕복은 타이머 밖이다. 형상 평가기의 요청 검사와 SLO별 결과 구성은 양쪽에 포함한다. 단위는 Python 3.9.25·scikit-learn 1.6.1의 로컬 warm CPU 함수 시간이다.

| 지표 | 매번 예측 | 예측 재사용 | 사전 기준 |
| --- | ---: | ---: | --- |
| 25질의 작업군 중앙 시간 | 556.08ms | **31.08ms** | 최소 3배 개선; **17.89배** |
| 첫 질의 p95 | 25.42ms | 26.27ms | 증가 ≤20%; **3.36% 증가** |
| 이후 24질의 p50 | 22.03ms | **0.338ms** | 보조 지표 |
| 이후 24질의 p95 | 24.50ms | **0.449ms** | 보조 지표 |

전체 출력 비교에는 상태, `ABSTAIN` 이유, 후보 순서와 예측값, 지연·요청/초 여유, 최소 GPU→CPU 선택, 출처 정보가 모두 포함된다. **1,125개 고유 질의 × 5회 = 5,625쌍에서 불일치 0건**이었다. 첫 반복의 고유 질의 중 `RESEARCH_ESTIMATE`는 720건, `ABSTAIN`은 405건이었다. 다섯 반복에서 후보 예측 캐시는 225회 계산하고 5,400회 적중했다(적중률 96.0%). 캐시 엔트리의 JSON 직렬화 길이는 중앙 1,525B, 최대 1,532B였다. 이 길이는 Python 객체 메모리 사용량이나 서버 캐시 비용이 아니다.

사전 통과 조건은 **전체 출력 동등성**, 25질의 묶음 중앙 시간 3배 이상 개선, 첫 질의 p95가 기본 경로보다 20% 이상 길지 않을 것이었다. 세 조건 모두 통과했다. 첫 질의는 캐시 키 구성·보관 비용 때문에 조금 느리며, 실제 사용에서 같은 작업의 SLO가 여러 번 바뀌는 비율을 모르므로 17.89배를 서비스 평균 지연 개선으로 환산할 수 없다.

## 재현과 경계

- [실험 코드](../scripts/evaluate_slo_reuse.py) SHA-256 `b163cd6f83b317815db6b59a594715092551bee5dc1cc2de70cd17541b68737b`; [고정 작업군](../artifacts/slo_reuse_v1/manifest.json) SHA-256 `dcea7d8dc21ab6928da990d0fd3a4cfb6883fe1d01a831462c214d059d8ba679`.
- [전체 지표](../artifacts/slo_reuse_v1/metrics.json) SHA-256 `d89d6b1c03a04e2284749b7f10553a5c9b9936d178355c5930536a92302f3b06`; 질의별 시간 (`../artifacts/slo_reuse_v1/timings.csv`) 11,250행; 작업군별 시간 (`../artifacts/slo_reuse_v1/block_timings.csv`) 450행. 모델 SHA-256은 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`, 원본 측정 SHA-256은 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`이다.
- 모델 채점 전 `PYTHONPATH=/home/researcher/ai-mcmp/research /usr/bin/python poc/optimization_research/scripts/evaluate_slo_reuse.py freeze`로 manifest를 별도 저장했고, 그 다음 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=/home/researcher/ai-mcmp/research /usr/bin/python poc/optimization_research/scripts/evaluate_slo_reuse.py evaluate`를 실행했다. 기존 manifest가 다르거나 없으면 재사용하지 않는다. 재실행 시 CPU 시간은 달라질 수 있다.
- [테스트](../tests/test_slo_reuse.py) 5개가 `/usr/bin/python3 -m pytest -q poc/optimization_research/tests/test_slo_reuse.py`와 저장 모델에 맞는 `/usr/bin/python -m unittest discover -s poc/optimization_research/tests -p 'test_slo_reuse.py' -v`에서 각각 통과했다. 테스트는 모델 복사 경로에서 1 miss/24 hit, 입력·지원 범위·원래 모델 변경 시 캐시 무효화, 보류 사유 동등성을 검사한다.

이 경로는 동일 호스트의 이미 지원하는 모델에 대한 **배치 벤치마크 점추정**을 빠르게 재평가할 뿐이다. 원본 SLO는 수집기 생성 목표이고 25개 변형도 합성이다. 정적 예측기의 `RESEARCH_ESTIMATE`는 실제 CPU/GPU VM 형상, 노드 수, `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 실행 승인이 아니다. 새로운 배포·스케일 효과 학습에는 [명령과 사용자 결과의 연결 기록](scale_effect_data_contract_v1.md)이 필요하다.
