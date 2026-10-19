# 동일 작업 SLO 묶음 평가 API 검증

2026-09-28. [설계안](slo_reuse_api_design.md)의 호출 범위 재사용을 [오프라인 8후보 평가기](../scripts/assess_gpu_shape.py)에 `assess_gpu_shape_many(bundle, requests)`로 구현했다. 단건 `assess_gpu_shape`와 CLI의 입력·출력은 유지하고, 두 공개 함수가 동일한 요청 검사·점수 계산 본문을 호출한다. 새 함수는 같은 순서의 후보 8개가 다시 들어오면 **원시 모델 예측만** 재사용한다. 지연 상한·요청/초 하한은 매 요청 검증하고 다시 평가한다. 전역 함수 교체나 파일 저장 캐시는 없다.

## 검증 결과

[사전 적격성·평가 규약](scale_decision_feasibility.md)의 저장 모델·원본·45작업군 manifest를 유지했다. 원본 9개 모델에서 모델별 5작업군을 택하고, 각 작업군의 생성 SLO를 5×5배수로 바꾼 **25개 합성 요청**을 사용했다. 이들은 실제 사용자 반복 요청이 아니다. 모델을 예열하고 Python 3.9.25·scikit-learn 1.6.1에서 작업군과 비교 경로 순서를 고정 난수로 섞어 **5회** 측정했다. 모델 로드, 파일/JSON 입력 및 네트워크 왕복은 시간에서 제외했다.

| 측정치 | 매번 단건 평가 | 새 묶음 API | 판정 |
| --- | ---: | ---: | --- |
| 25질의 작업군 중앙 함수 시간 | 487.01ms | **24.19ms** | **20.13배**, 최소 3배 기준 통과 |
| 한 요청만 넣은 별도 호출 p95 | 22.58ms | 22.72ms | **0.62% 증가**, 최대 20% 기준 통과 |

한 요청 시간은 25질의 묶음 밖에서 `assess_gpu_shape(bundle, first_request)`와 `assess_gpu_shape_many(bundle, [first_request])`를 따로 재었다. 두 경로 모두 예열된 모델로 후보 예측을 새로 계산한다. 따라서 이 수치는 API 진입 오버헤드이며 서버의 cold start나 묶음 안의 첫 요청 응답 시간을 뜻하지 않는다. 묶음 API는 리스트 전체가 완료된 뒤 결과를 반환한다.

두 경로의 **전체 출력**은 45×25×5 = **5,625쌍에서 불일치 0건**이었다. 첫 반복의 1,125개 고유 합성 요청에서는 `RESEARCH_ESTIMATE` 720건, `ABSTAIN` 405건이었다. 단위 테스트는 25개 SLO의 단건 대비 출력·예측 호출 수 `25→1`, 중간의 잘못된 요청, 지원 범위 밖 후보, 다른 후보 그리드와 순서, 응답 사이 가변 사유 리스트 분리, 모델·지원 규칙 변경 뒤 새 호출, 최대 64그리드 캐시 제거를 검사했다. 저장 모델 환경의 [새 API 테스트](../tests/test_assess_gpu_shape_many.py) **6개**가 통과했고, 그중 하나가 고정된 **45×25 전체 출력**을 다시 비교했다. 기존 단건 테스트 8개와 기존 재사용 실험 테스트 5개도 같은 환경에서 통과했다.

이 결과는 새 API의 **별도 측정**이다. [기존 임시 가로채기 실험](slo_reuse_findings.md)의 17.89배를 재사용하지 않았다. 새 함수는 캐시 키 직렬화와 예측 결과 복사, 최대 64개 그리드의 LRU 제거를 포함한다. 캐시는 **한 `assess_gpu_shape_many` 호출에서만** 살아 있고, 반환·예외 시 폐기한다. 모델·지원 규칙은 호출 중 읽기 전용이어야 한다. 호출자가 이후 모델이나 후보를 바꾸면 새 호출에서 예측을 다시 계산한다. 공유 장기 캐시나 서비스 동시성은 검증하지 않았다.

응답의 `provenance.hashes_verified=true`는 `load_verified_bundle`이 디스크 모델·원본의 SHA를 로드 전에 확인했고 묶음의 검증 마커가 유지됐다는 뜻이다. 평가기가 **이후 메모리 안에서 바뀐 모델·지원 범위를 재해시한다는 뜻은 아니다**. 측정과 동등성 검사는 로더가 반환한 묶음을 변경하지 않는 조건에서 수행했다.

## 재현 자료

- [벤치마크 코드](../scripts/benchmark_slo_reuse_many.py) SHA-256 `33ef546f447fb618dd7cada6993cf917aef43a0c81bfdabe3effc5d1af3859a6`; [평가기 코드](../scripts/assess_gpu_shape.py) SHA-256 `ece3f8ac5c5c410bae9afa5df3434c28efdcdfe3b9a47137e70749a918162959`.
- [새 지표](../artifacts/slo_reuse_many_v1/metrics.json) SHA-256 `2dc008f6198fdfe6e0c6c98efba493ecbd21de125e2f5c4600f95266d1376c11`; 묶음 시간 (`../artifacts/slo_reuse_many_v1/block_timings.csv`) 450행, 한 요청 시간 (`../artifacts/slo_reuse_many_v1/first_request_timings.csv`) 450행. CSV의 중앙값·p95와 행 수를 별도로 재계산해 지표 파일과 맞음을 확인했다.
- 원본 측정 SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`; 저장 모델 SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`; [고정 manifest](../artifacts/slo_reuse_v1/manifest.json) SHA-256 `dcea7d8dc21ab6928da990d0fd3a4cfb6883fe1d01a831462c214d059d8ba679`; [당시 동결 규약 원문](../artifacts/slo_reuse_v1/frozen_protocol.md) SHA-256 `b81fc60da43e5df7ecb11b3d560b24fd5f89b158c2bad360a1aae448385a817a`. 시간 측정 전 네 파일의 해시와 manifest에 기록된 규약 해시를 확인했다. 현재 [설명 문서](scale_decision_feasibility.md)에는 이후 PAX 해석 문단이 추가돼 해시가 다르다. `verify_frozen_manifest`는 보관 원문과 선택된 45개를 검증하며, `verify_manifest`는 현재 문서 변경을 계속 검출한다.

```bash
cd /home/researcher/ai-mcmp
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  PYTHONPATH=/home/researcher/ai-mcmp/research \
  /usr/bin/python -m unittest discover -s poc/optimization_research/tests \
  -p 'test_assess_gpu_shape_many.py' -v
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  PYTHONPATH=/home/researcher/ai-mcmp/research \
  /usr/bin/python poc/optimization_research/scripts/benchmark_slo_reuse_many.py
```

이 API는 **같은 호스트의 이미 지원되는 모델과 벤치마크 후보**에 대한 합성 SLO 탐색을 빠르게 한다. GPU 값은 같은 호스트의 프로세스 복제 수, CPU 값은 affinity 코어이며 실제 VM 노드·GPU 수·배포 조치가 아니다. 실제 반복 요청 빈도와 `DEPLOY/UPDATE/SCALE_OUT/SCALE_IN/KEEP` 결과 자료가 없으므로 20.13배를 서비스 평균 지연 개선 또는 배포 안전성으로 해석하지 않는다.
