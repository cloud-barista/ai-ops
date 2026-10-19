# 동일 후보·다른 SLO 평가의 오프라인 API 설계

2026-09-28. 구현에 앞서 고정한 설계안이다. [기존 형상 평가기](../scripts/assess_gpu_shape.py)와 [재사용 실험](../scripts/evaluate_slo_reuse.py)을 읽고 작성했다. 실험의 17.89배 수치는 `unittest.mock.patch.object`로 예측 함수를 임시 교체한 **기존 실험 경로**의 측정치다. 새 API의 수치는 별도 측정으로 보고한다.

## 제안: 호출 범위가 명확한 묶음 API

`assess_gpu_shape_many(bundle, requests: list[dict]) -> list[dict]`를 `assess_gpu_shape.py`에 추가한다. 연구 스크립트는 검증된 저장 모델을 한 번 불러오고, 같은 작업의 25개 SLO 요청을 하나의 리스트로 넘긴다. 함수는 입력 순서대로 **기존 `assess_gpu_shape(bundle, request)`를 각 요청에 호출했을 때와 값이 완전히 같은 결과**를 반환해야 한다. 8후보 그리드가 다른 요청이 섞여 있어도 허용하고, 후보가 같을 때만 원시 예측을 재사용한다. 빈 리스트는 빈 리스트를 반환한다. 리스트가 아닌 최상위 인자는 호출 오류로 처리하되, 리스트 안의 잘못된 요청은 기존 단건 평가기와 같은 `ABSTAIN` 결과를 낸다.

단건 평가기의 현 본문을 비공개 `_assess_gpu_shape(bundle, request, predict_candidates)`로 옮긴다. 공개 단건 함수는 `_predict_once`를 그대로 주입한다. 새 묶음 함수는 **호출 지역** 예측 제공자를 주입한다. 그러면 요청 검사, 검증된 아티팩트 검사, 지원 범위 검사, 지연·요청/초 변환, 후보 점수, GPU→CPU 순위, `ABSTAIN` 사유, 출처 필드를 **같은 코드**가 매 요청마다 만든다. `predict_records`의 모듈 변수를 바꾸거나 `unittest.mock.patch`를 사용할 필요가 없다. 기존 CLI와 `assess_gpu_shape` 시그니처는 유지한다.

```python
def assess_gpu_shape(bundle, request):
    return _assess_gpu_shape(bundle, request, _predict_once)

def assess_gpu_shape_many(bundle, requests):
    if not isinstance(requests, list):
        raise TypeError('requests_must_be_list')
    raw_by_grid = OrderedDict()  # this invocation only, at most 64 grids

    def predict_for_request(active_bundle, candidates):
        # _assess_gpu_shape calls this only after the original request,
        # verified-bundle and model-schema checks have passed.
        key = canonical_ordered_candidate_json(candidates)
        if key not in raw_by_grid:
            raw_by_grid[key] = copy.deepcopy(_predict_once(active_bundle, candidates))
            if len(raw_by_grid) > 64:
                raw_by_grid.popitem(last=False)
        return copy.deepcopy(raw_by_grid[key])

    return [_assess_gpu_shape(bundle, request, predict_for_request)
            for request in requests]
```

위 코드는 경계 설명용이다. 핵심은 예측 **결과만** 저장하고 최종 평가 결과를 저장하지 않는 것이다. `latency_p95_ms`와 `examples_per_second`는 SLO 입력이 아니므로 같은 후보 그리드에서 재사용할 수 있다. 반면 `batch_latency_p95_limit_ms`와 `min_requests_per_second`는 모든 요청에서 다시 검증·점수화한다. 후보가 지원되지 않는 경우의 `ABSTAIN` 사유도 저장된 원시 결과에서 매번 새 결과 객체로 복사해야 한다. `assess_gpu_shape`는 현재 `outcome['reasons']` 리스트를 결과에 넣으므로, 캐시에서 복사하지 않으면 두 응답이 같은 리스트를 공유한다.

## 키와 무효화

- 키는 **검증을 통과한 후보 8개의 전체 필드와 순서**를 `json.dumps(..., sort_keys=True, separators=(',', ':'), allow_nan=False)`로 정규화한 문자열로 둔다. 해시만 저장할 경우 드물더라도 충돌 검사가 필요하므로 이 작은 8후보 입력은 원문 정규화 문자열을 키로 쓰는 편이 단순하다. `default=str`은 타입을 합쳐 버릴 수 있어 쓰지 않는다. `application_id`, `workload_id`, 호스트·장치, 모델·revision, batch/length/concurrency, CPU/GPU 수, repeats 모두 포함한다. SLO 두 필드만 키에서 빠진다.
- 모델/원본 SHA, 피처·지원 규칙은 같은 `bundle`에 대해 함수가 한 번 실행되는 동안 고정되어야 한다. 기존 `load_verified_bundle`의 SHA·scikit-learn 버전 검사는 모델 로드 시 한 번 실행하고, 단건 평가기의 `VERIFIED_KEY`·스키마 검사는 매 요청 그대로 실행한다. 모델은 묶음 호출 중 재학습·교체하지 않는 **읽기 전용 입력**으로 계약한다. 다른 모델이나 지원 규칙을 적용할 때는 새 `assess_gpu_shape_many` 호출을 한다.
- 응답의 `provenance.hashes_verified=true`는 로더가 **디스크 아티팩트를 읽기 전에** SHA를 확인했고 묶음에 검증 마커가 있다는 뜻이다. 이후 전달받은 메모리 안의 모델·지원 범위를 매 요청 재해시했다는 뜻은 아니다. 로더가 반환한 묶음을 수정하지 않는다는 전제가 깨지면 재검증된 모델을 새로 로드해야 한다.
- 함수 반환 또는 예외와 함께 캐시를 폐기한다. 호출 안에서는 최근 64개 그리드만 보관하고 오래된 항목을 제거한다. 따라서 이전 호출의 결과가 바뀐 모델, 원본, 지원 규칙 또는 후보에 남는 장기 캐시 무효화 문제가 없다. 이 방식은 사용자가 요청을 모아 넘길 수 있는 **오프라인 what-if 평가**에 맞다. 요청이 시간에 흩어져 도착하는 서비스 캐시나 스레드 간 공유 캐시는 이 API의 범위가 아니다. 그런 수요가 실측되면 별도 세션 객체, 아티팩트 버전 전환, 동시성 규칙을 설계해야 한다.
- 엄밀한 동등성 보장은 묶음 호출 중 모델·지원 규칙을 다른 스레드가 변경하지 않는 조건이다. 외부에서 전달받은 Python `dict`와 fitted model을 임의로 동시 변경하는 상황까지 캐시 키만으로 안전하게 처리할 수 없다. 이를 운영 환경으로 확대할 때는 세션이 검증된 아티팩트를 독점 보유하고 버전 변경 시 세션 전체를 교체해야 한다.

## 검증 계획과 채택 기준

1. 기존 [단건 평가기 테스트](../tests/test_assess_gpu_shape.py)를 그대로 통과시킨다. 새 단위 테스트는 25개 SLO에서 단건 리스트 컴프리헨션과 묶음 API의 **전체 출력 값**이 같고, 원시 모델 호출 수가 `25→1`인지 확인한다. `RESEARCH_ESTIMATE`, SLO 미충족, 지원 범위 밖, 입력 오류, 미검증 묶음을 각각 포함한다.
2. 다른 후보 배열, `model_revision`, host/device, batch/length/concurrency, 후보 순서 및 지원 규칙 변경은 새 예측 호출을 만들거나, 새 묶음 호출에서 캐시가 없어야 한다. 잘못된 요청은 캐시 적중·예측을 만들지 않아야 한다. 두 응답 중 하나의 `reasons` 리스트를 수정해도 다른 응답은 변하지 않아야 한다. 빈 리스트와 잘못된 최상위 인자도 확인한다.
3. 실제 고정 저장 모델과 [기존 45작업군 manifest](../artifacts/slo_reuse_v1/manifest.json)로 1,125개 고유 SLO 요청을 평가한다. 단건 경로와 새 API의 출력은 후보 순서·숫자·사유·출처까지 **0건 불일치**여야 한다. 저장 모델과 일치하는 Python 3.9.25·scikit-learn 1.6.1 환경에서 확인한다. 테스트의 가짜 모델만으로 저장 모델 호환성을 주장하지 않는다.
4. [기존 사전 규약](scale_decision_feasibility.md)의 동일한 45작업군·25질의·5회 반복으로 **새 API를 별도로 측정**한다. 기존 실험의 17.89배를 새 API 결과로 옮겨 쓰지 않는다. 25질의 묶음 전체 시간과 첫 질의만 담은 단건/묶음 호출 시간을 각각 측정한다. 두 단건 측정은 전체 묶음 타이머 밖에 둔다. 연구용 채택 기준은 전체 출력 동등성, 25질의 묶음 중앙 시간 3배 이상 개선, 한 요청 호출 p95 증가 20% 이하다. 새 API가 이 기준에 못 미치면 최적화 승격을 보류하고 결과를 남긴다.

## 실제 가치와 우선순위

이 API가 생기면 다른 연구 스크립트가 같은 작업의 SLO 범위를 평가할 때 벤치마크의 monkeypatch 없이 검증된 재사용 경로를 호출할 수 있다. 현재 1,125개 요청은 **원본의 한 생성 SLO를 변형한 합성 질의**이고 실제 반복 사용자 요청 자료는 없으므로, 운영 수요나 서비스 지연 절감 효과는 입증되지 않았다. 알고리즘의 새 성능·배포 안전성도 늘지 않는다. 가치의 범위는 **재현 가능한 오프라인 추론 비용 절감 API**다. 이 범위에서 구현·테스트는 타당하지만, 새로운 형상 안전성 검증이나 실제 `SCALE_OUT/IN` 행동 자료를 확보하는 일을 대신할 연구 성과로 쓰면 안 된다.
