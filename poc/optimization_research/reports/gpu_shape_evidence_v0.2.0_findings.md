# 물리 GPU 인덱스와 입력 조합 출처를 붙인 정적 연구 증거 v0.2.0

2026-09-29. [기존 v0.1](gpu_shape_evidence_v0.1.0_findings.md)의 저장 모델·평가기·증거를 바꾸지 않고, [v0.2 직접 생성기](../scripts/generate_gpu_shape_evidence_v0_2.py), [스키마](../contracts/gpu_shape_evidence_v0.2.0.schema.json), [독립 검증기](../scripts/validate_gpu_shape_evidence_v0_2.py)를 추가했다. 이 결과는 **같은 `icns` 호스트의 배치 벤치마크 추정치**이며 VM 자원 형상이나 배포·스케일 명령이 아니다.

## 확인한 지원 범위와 바뀐 입력

고정 추출 자료 (`../data/audit_v1/measurements.parquet`)의 `khu-hf-local-exhaustive` 적격 CUDA 4,600행은 575개 입력 조건마다 GPU 복제본 1/2개 × CPU affinity 2/4/8/16코어의 완전한 8후보다. GPU 1개 행 2,300건의 물리 인덱스는 모두 `[0]`, GPU 2개 행 2,300건은 모두 `[0,1]`이다. 이전 저장 번들 (`../artifacts/performance_v1/performance_model.joblib`)의 학습 특징·지원 규칙과 [v0.1 요청](../examples/gpu_shape_request.json)에는 물리 인덱스가 없었다. 그래서 v0.1은 같은 장치 이름을 가진 다른 GPU 인덱스를 구별할 수 없었다.

[v0.2 요청 예시](../examples/gpu_shape_request_v0_2.json)는 후보마다 `gpu_indices` JSON **정수 배열**을 요구한다. 복제본 1개는 정확히 `[0]`, 2개는 정확히 `[0,1]`만 받는다. 누락, `[1]`, 잘못된 길이, `false`, `0.0`, 문자열은 모델 로드·예측 전에 전체 `ABSTAIN`이다. [출력 예시](../artifacts/gpu_shape_evidence_v0_2_0/example_evidence.json)의 모든 후보와 선택 형상에도 이 인덱스를 명시했다. 생성기는 인덱스를 검사한 뒤 v0.1 평가기에 기존 후보 필드만 전달하므로 저장 모델의 수치와 8후보 선택 규칙은 그대로다.

요청의 최상위 키도 `batch_latency_p95_limit_ms`, `min_requests_per_second`, `candidates` 세 개만 허용한다. 예를 들어 `price_usd_per_hour_max`를 추가하면 가격 제약을 무시한 선택을 반환하지 않고 모델 로드 전에 `ABSTAIN`한다.

이 인덱스는 **호출자가 선언한 후보 장치**다. 이 오프라인 함수에는 실제 GPU 배치·실행 로그가 연결되지 않아, 호출자가 나중에 다른 장치를 사용했는지 인증하지 않는다. 응용 ID와 작업 ID도 등록 시스템과 별도 대조하지 않는다.

저장 번들의 입력 범위 검사는 각 정수의 최소·최대와 `model_id × revision × host × device × device_name × replica_count` 조합을 확인한다. **입력값의 공동 조합까지 학습에서 관측했는지는 검사하지 않는다.** 기존 평가가 지원한 별도 `novel-matrix`의 CUDA 80행/10그룹과 `space-filling`의 264행/33그룹은 344행 모두 원래 학습의 공동 특징 튜플에 없었다. 그중 각각 72행·128행은 학습에 없는 개별 `max_length` 또는 `repeats` 값이 수치 범위 안이라는 이유로 지원됐다. 나머지 144행은 개별 값은 관측됐지만 그 조합이 새롭다. 이 43개 그룹은 새 입력 조건 평가의 핵심이므로 v0.2는 공동 조합 미관측을 선택 금지로 바꾸지 않는다.

## `support_relation`의 정확한 의미

v0.2는 모델 예측과 별도로 `support_relation.partition`을 `train`, `validation`, `test`, `novel_matrix`, `space_filling`, `unknown` 중 하나로 기록한다. 이는 **모델 ID·revision·호스트·장치·batch·최대 길이·동시성·반복 수의 정확한 튜플**을 고정 추출 자료와 분할 manifest에 대조한 결과다. 호출자가 쓴 `application_id`나 `workload_id`로 출처를 정하지 않는다. `unknown`은 이 **다섯 추적 분할에 같은 튜플이 없음**을 뜻하며, 세상의 모든 측정이나 다른 캠페인에 없다는 뜻은 아니다. `support_relation`은 선택식에 입력되지 않고, `independent_performance_proof=false`다. 후보별 8형상 완전성과 CUDA 인덱스는 출처 색인을 만들 때 다시 확인한다.

고정 분할의 공동 입력 조건은 train **396**, validation **89**, test **90**, 별도 novel-matrix **18**, space-filling **44**개이며, 637개 키에 분할 충돌은 없다. [예시 증거](../artifacts/gpu_shape_evidence_v0_2_0/example_evidence.json)의 `partition=train`, 선택 2복제본/CPU affinity 2코어/`[0,1]`는 **학습에 포함된 입력 조건의 형식 시연**이다. 홀드아웃 정확도 확인 사례가 아니다. 이미 보고한 정적 성능 모델의 시험·별도 캠페인 오차는 [원 실험](../artifacts/performance_v1/experiment_report.md)에 그대로 있다.

생성기는 모델 파일 SHA-256 `7a934da440ea0c7473e4e59eb5fa0c8c57f347389272bd303cb0051ef918b161`, 추출 Parquet SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`, 분할 manifest SHA-256 `817ccd79e180e0373db085ac254698b26c258b060aea633bcc73c621f86648f8`을 검사한다. 출처 색인은 확인한 파일 **바이트**에서 만든다. v0.2의 `evidence_id`는 생성 시각·ID 자체를 제외한 전체 증거의 정규 JSON SHA-256이며, 인증 서명은 아니다.

기본 검증은 스키마·수치·선택 규칙·물리 인덱스·출처 분할·ID의 **구조적 일관성**을 확인한다. 원 요청을 함께 주면 요청 해시와 앱/작업·모델·입력 조건·목표·후보 형상도 대조하지만 모델 예측을 재실행하지 않는다. 예를 들어 한 GPU 후보의 처리량과 여유값을 함께 바꾸고 공개 ID를 다시 계산하면, 2 GPU 선택을 1 GPU로 바꾼 증거도 기본 검증을 통과한다.

수신자가 모델 출력까지 확인해야 할 때는 `--strict-replay`와 **원 요청**을 함께 준다. 이 경로는 고정 모델·추출 자료·분할 manifest의 해시를 확인하고 같은 요청·생성 시각·증거에 적힌 목표 출처로 증거를 재생성한 뒤 전체 객체를 비교한다. 위 1 GPU 변조는 `pinned_model_replay_mismatch`로 거절된다. 사전 입력 검사에서 나온 부분 `ABSTAIN`에도 고정 아티팩트 검사를 적용한다. 목표 출처는 여전히 호출자가 선언한 값이므로 그 출처의 진위는 별도의 신뢰 가능한 입력과 대조해야 한다. 재실행 일치는 실제 사용자 SLO 또는 VM 배포 효과의 독립 검증이 아니다.

## 재현

학습 당시 의존성이 설치된 `/usr/bin/python`에서 연구 루트를 작업 디렉터리로 사용한다. 기존 예시 파일을 덮어쓰지 않도록 새 출력 경로를 지정한다.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/generate_gpu_shape_evidence_v0_2.py \
  --request examples/gpu_shape_request_v0_2.json \
  --objective-source examples/gpu_shape_objective_source.json \
  --generated-at 2026-09-28T19:46:08.278489Z \
  --output /tmp/gpu_shape_evidence_v0_2_example.json
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/validate_gpu_shape_evidence_v0_2.py \
  --input /tmp/gpu_shape_evidence_v0_2_example.json \
  --request examples/gpu_shape_request_v0_2.json
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/validate_gpu_shape_evidence_v0_2.py \
  --input /tmp/gpu_shape_evidence_v0_2_example.json \
  --request examples/gpu_shape_request_v0_2.json \
  --strict-replay
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  -m unittest discover -s tests -p 'test_generate_gpu_shape_evidence_v0_2.py' -v
```

현재 v0.2 테스트 **9개**는 실제 저장 모델의 v0.1 후보 수치 동등성, 물리 인덱스·미지원 최상위 제약의 사전 보류, 다섯 분할의 정확 입력 매칭, 결과·선택·분할·수치·앱 ID 변조 거절, 분할 manifest 변경 시 모델 로드 전 종료를 확인한다. 추가로 2 GPU→1 GPU 변조가 기본 검증을 통과하고 엄격 재실행에서 거절되는지, 부분 `ABSTAIN`에서도 잘못된 고정 모델 파일을 거절하는지 확인한다. 기존 생성기의 기본 경로는 모델을 한 번만 실행한다. 기존 v0.1 스키마·예시·직접 생성 코드는 변경하지 않았다. v0.1 예시 SHA-256은 기존 `07a0aa88a00685941541a49b33fd9587e86c27d23d32a2f2c2173b4a7c93b067` 그대로다.

이 경로에는 사용자 end-to-end 지연, 가격·가용성, VM CPU/메모리·노드 수, 다른 GPU 인덱스/호스트, 실제 조치 결과가 없다. 따라서 `RESEARCH_ESTIMATE`나 `support_relation`을 Go `ResourceRecommendation.feasible`, `DesiredDeploymentSpec`, `DEPLOY`/`SCALE_OUT` 승인으로 변환하지 않는다.
