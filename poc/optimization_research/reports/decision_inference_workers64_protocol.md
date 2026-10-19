# 64격자 묶음 예측의 ExtraTrees worker 수 비교 규약

2026-09-29. [기존 1/2-worker 측정](performance_predict_workers_findings.md)은 후보 1·8행에서 1 worker가 빨랐다. [64격자 묶음 채택](decision_inference_chunk64_findings.md) 후 한 호출이 최대 **512후보**를 담으므로, 같은 선택이 여전히 타당한지 다시 확인한다. 아직 새 크기에서 2·4 worker의 출력 또는 시간을 보지 않았다.

## 동등성과 측정

- [v5 연구 경로](../scripts/assess_gpu_shape_microbatch_v5.py), 고정 저장 모델·Parquet·분할 manifest, `max_grids=64`를 쓴다. 후보는 기존 45격자×25합성 SLO의 공유·복사 형태 및 서로 다른 575격자다.
- 기존 `predict_records`는 예측 직전 ExtraTrees `n_jobs=1`을 강제한다. 이 시험에서는 동일 경로의 설정 함수만 호출 동안 바꿔 **1·2·4 worker**를 각각 설정한다. `_predict_once`가 회귀기만 얕게 복사하므로 학습된 트리·저장 모델·공유 bundle은 수정하지 않는다. 예측값이나 최종 SLO 선택 코드를 수정하지 않는다.
- 먼저 세 입력의 **정규 JSON 전체가 worker 1과 정확히 같은지** 검사한다. 조금이라도 다르면 후보 worker의 속도 점수는 채택에 사용하지 않고 `NO_GO_EXACT_OUTPUT`으로 기록한다. 배치 예측 실패의 원래 격자별 재시도는 v5에 남는다.
- 출력이 같을 때만 모델 적재·첫 완전 호출을 타이머 밖에 두고, 입력별로 7번의 짝지은 wall/CPU 시간을 측정한다. 각 반복마다 요청 블록을 고정 시드로 섞고 worker 실행 순서를 고정된 6개 순열을 순환해 바꾼다. 각 worker의 같은 반복 `1 worker wall / 후보 wall` 비율 7개 중앙값을 쓴다. BLAS/OMP/MKL은 한 worker로 제한한다.
- peak RSS는 각 입력×worker를 **별도 프로세스**에서 확인한다. 1 worker 대비 20 MiB 넘게 증가하면 후보를 채택하지 않는다.

## 선택 기준

2·4 worker 각각에 대해 출력 동등성, 복사 입력 중앙 속도비 **≥1.10**, 공유·고유 입력 각각 **≥0.90**, 세 입력의 peak RSS 증가 **≤20 MiB**를 요구한다. 통과 후보가 여러 개면 세 입력 속도비의 최솟값이 높은 worker를 고르고, 동률이면 적은 worker를 고른다. 아무 후보도 통과하지 못하면 현재 **1 worker**를 유지한다. 이는 저장된 한 모델의 warm 오프라인 계산 선택이며 온라인 사용자 지연이나 다른 모델의 최적 worker 수를 뜻하지 않는다.
