# 저장 성능 모델·GPU 형상 평가기 후속 무결성 감사

> 이 문서는 수정 전 오류 재현과 권고를 기록한다. 모델·revision·장치별 범위를 적용한 현재 동작과 변경 후 수치는 [수정 결과](saved_model_condition_support_fix.md)를 따른다.

## 결론

`performance_v1`의 원본·모델·훈련 코드 해시와 작업 그룹 분할은 현재 파일에서 일치한다. **수정할 결함은 추론 지원 범위**다. 저장 모델은 숫자형 조건을 전체 9개 모델의 훈련 최솟값·최댓값으로만 검증한다. 따라서 해당 모델에서 한 번도 측정하지 않은 부하 설정도 `PREDICTED`가 되고, GPU 형상 평가기는 합성 목표에 따라 `RESEARCH_ESTIMATE`까지 반환한다. 같은 전역 지원 판정은 별도 캠페인의 일부 모델별 범위 밖 조건을 “학습 범위 내”로 집계한다.

## 재현 가능한 오류

- 원본: `data/audit_v1/measurements.parquet` SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`; 저장 모델: `artifacts/performance_v1/performance_model.joblib` SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`.
- `artifacts/performance_v1/split_manifest.csv`에서 원 학습의 Qwen 적격 행은 352개다. Qwen 배치는 `{1,2,4,8}`, 동시성은 `{1,2,3,4}`, 반복 수는 `{6}`이다. 모든 적격 캠페인을 합쳐도 Qwen `batch_size=16` 실행은 0개다. 다른 모델의 배치 16 때문에 저장 번들의 `numeric_support.batch_size=[1,16]`이다.
- `examples/gpu_shape_request.json`의 모델·revision을 Qwen으로, 모든 8개 후보의 `batch_size=16`, `max_length=160`, `request_concurrency=2`, `repeats=6`으로 바꾸고, 지연 한도 `10^9 ms`, 최소 요청률 `0.01/s`를 주었다. 고정 원본과 모델을 `load_verified_bundle`로 읽어 `assess_gpu_shape`를 호출하면 **8개 모두 `PREDICTED`**, 전체 결과는 **`RESEARCH_ESTIMATE`**, 선택은 GPU 복제본 1개·CPU affinity 2코어였다. 이 수치는 물리 실측을 검증한 결과가 아니며, 입력 지원 판정의 재현 예다.
- 원인: `scripts/train_performance.py`의 번들 `numeric_support`는 훈련 전체의 컬럼별 전역 범위이고, `scripts/predict_performance.py`의 `predict_records`는 각 값이 그 범위 안인지 검사한다. `support_combinations`는 모델/revision/host/device/device_name/복제본 수만 묶으며, 모델별 batch·길이·동시성·반복 수는 포함하지 않는다. `scripts/assess_gpu_shape.py`는 predictor의 `PREDICTED`를 그대로 형상 점수화한다.

## 별도 캠페인 해석에 미치는 영향

기존 전역 게이트와 독립 그룹 제외를 따르면 `local-novel-matrix`는 108행/10그룹, `local-space-filling`은 296행/33그룹이 평가된다. 동일한 훈련 행에서 `(model_id, model_revision, device)`별 batch·길이·동시성·CPU·GPU 복제본 범위를 검사하고 **해당 모델에서 관측된 repeats 값만** 인정하면 각각 56행/5그룹, 100행/9그룹만 남는다. 차이의 대부분은 BERT류 훈련 반복 수 8에 대해 별도 캠페인에서 6 또는 7을 쓴 경우다. 범위만 모델별로 좁히고 repeats를 전역 6–8로 남기면 각각 104행/10그룹, 292행/33그룹이다. 그러므로 기존 별도 캠페인 결과 자체를 삭제할 필요는 없지만, 현재 문서의 “학습 범위 내”는 **전역 수치 범위 내 보간·모델별 반복 프로토콜 전이도 포함**한다고 밝혀야 한다. 엄격한 모델별 지원 집합 성능은 별도로 집계해야 한다.

## 최소 수정·검증 권고

1. 평가기에는 `(model_id, model_revision, device)`별 **훈련 분할에서만** 계산한 batch·길이·동시성 범위를 적용한다. 이 범위 안의 새 정수값은 보간 연구로 유지한다. `repeats`는 같은 모델의 훈련에서 실제 관측된 값만 안전 지원으로 인정한다. Qwen의 배치 16/동시성 6·8, BERT류의 repeats 6·7은 `ABSTAIN`해야 한다. 지원 근거가 없는 전체 `(batch,length,concurrency)` 조합까지 완전 실측이라고 주장하지 않는다.
2. 저장된 joblib을 재학습하거나 고치지 않고, 이미 해시 검증한 Parquet의 **같은 바이트**에서 원 훈련 분할(seed 42, `khu-hf-local-exhaustive`)을 재구성해 모델별 지원 맵을 메모리에 붙일 수 있다. 분할 명세 CSV만 단독으로 신뢰하지 않는다. 또는 모델·원본·훈련 스크립트 해시가 고정된 별도 지원 맵 파일을 핀한다.
3. `predict_records`의 직접 사용과 GPU 평가기 사용이 서로 다른 지원 판정을 갖지 않도록 단일 검사를 공유한다. 이미 저장한 외부 평가는 엄격 지원 그룹만 재집계하고, 나머지를 보간·프로토콜 전이 성능으로 명시한다. 결함 회귀 시험은 Qwen 배치 16, Qwen 동시성 6, BERT류 repeats 6·7, 정상 Qwen 배치 8/반복 6, 정상 BERT 배치 16/반복 8을 포함한다.

## 확인했으나 새 결함이 발견되지 않은 부분

- 저장 `metrics.json`의 모델·원본·훈련 스크립트 해시는 현재 파일과 일치한다. `train_performance.py`의 features에는 측정 이후의 지연·처리량·노드 텔레메트리·생성 목표가 들어 있지 않다. 평가기가 요청 후보의 결과 필드를 거부하는 것도 확인했다.
- 분할 명세 4,856행은 원본의 `(host, execution_id, workload_group)`과 모두 일치한다. 575개 작업 그룹이 train 396, validation 89, test 90에 서로 겹치지 않으며, 동일 자원 격자의 측정이 세 분할을 넘지 않는다. 이것은 그룹 중복 누수를 배제하지만, 같은 호스트에서 가까운 시각에 실행된 측정의 시간 상관이나 새 장비 일반화까지 증명하지 않는다.
- GPU 후보의 숫자는 **같은 호스트의 물리 GPU 복제본 수와 CPU affinity 코어**다. VM 수·GPU 종류 추천, 실제 서비스 요청 지연, 배포/스케일 효과, 가격 기준 선택의 정답은 아니다. `examples_per_second / batch_size` 단위 변환은 평가기에 구현되어 있다.
- `artifacts/performance_v1/verification.json`의 `scripts/predict_performance.py` SHA는 현재 파일과 다르다. 이 파일은 이전 검증 실행의 기록으로 해석해야 하며 현재 predictor 버전 검증의 증거는 아니다. 모델·훈련 스크립트 해시에는 불일치가 없다.

검사 환경: `/usr/bin/python`, NumPy 2.0.2, pandas `2.3.0+4.g1dfc98e16a`, scikit-learn 1.6.1, joblib 1.5.1. 일반 `python3`은 이 환경에서 다른 버전이므로 고정 평가기 로더가 버전 불일치로 안전하게 보류한다.
