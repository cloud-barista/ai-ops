# Google 2011: 배정된 자원 아래 30분 관측 최대 사용량 예측 규약

작성일: 2026-09-28. 이 규약의 특징, 후보, 선택 규칙, 시험 판정은 `00200` 검증 점수를 계산하기 전에 고정했다. 앞선 [연결성 진단](google_2011_resource_feasibility.md)에서 `00400`의 적격 수, job 쏠림, 관측 최대의 요청 초과 건수 등 시험 정답 집계 일부가 이미 공개되어 있었다. 따라서 완전 블라인드 시험은 아니다. **이 실험의 후보별 `00400` 예측 손실·피복률은 규약 고정 시 미열람**이었다.

## 연구 질문과 모집단

Google Borg의 원래 요청·배정 아래, 같은 기계에서 중단 없이 30분 살아남고 유효한 `task_usage` 구간이 95% 이상 겹치는 task에서 기록될 CPU·RAM 최대값의 조건부 90분위수를 예측한다. 사용량 행이 예측 구간과 부분적으로 겹칠 수 있어 이 정답은 정확한 30분 연속 계측 피크가 아니라 **겹치는 보고 구간의 최대값**이다. `aggregation type=1` 행이 CPU 또는 RAM 최대값을 만든 에피소드는 제외한다. 생존과 피복은 사후 평가 모집단을 정하는 조건이며 입력 특징이 아니다.

분할은 시간순 파일 `00050` 학습, `00200` 검증, `00400` 최종 시험이다. 학습·검증·시험에 공통으로 [감사 스크립트](../scripts/audit_google_2011_resource_feasibility.py)의 단일 `SUBMIT`, 10분 이내 단일 `SCHEDULE`, 요청 불변, 동일 기계, 창 내 상태 변경 없음, 유효 사용량 5행 이상, 중복 없이 95% 시간 피복 규칙을 적용한다. 여섯 원본의 SHA-256은 공식 `SHA256SUM`과 대조한다. 시험 결과는 학습·선택에 사용하지 않는다. 세 조각 사이 동일 job ID는 없다는 연결성 진단 결과를 사용한다.

## 입력과 후보

입력은 `SUBMIT` 사건에 기록된 CPU 요청, RAM 요청, scheduling class, priority 네 값뿐이다. 요청값의 `log1p` 변환은 허용한다. 기계 ID, 사용량, 실제 생존 시간, 집계 유형, 시험 시점의 통계량, 제출 후 생성된 값은 입력하지 않는다.

CPU와 RAM을 **각각** 별도 예측한다. 모든 분위수는 `q=0.90`, 모든 후보는 학습 조각만으로 맞춘다.

1. `request`: 해당 차원의 원래 요청값 그대로.
2. `global_ratio`: 관측 최대/해당 요청의 job 균등 가중 90분위수를 학습하고 그 비율을 새 요청에 곱한다.
3. `conditional_ratio`: 학습 CPU·RAM 요청의 각 4분위 경계로 4×4 셀을 만든다. 셀에 학습 job 3개 이상과 행 20개 이상이면 해당 차원의 job 균등 가중 최대/요청 90분위수를 사용한다. 그렇지 않으면 해당 차원 요청의 한 축 bin을 쓰고, 거기도 모자라면 전체 `global_ratio`로 후퇴한다. 경계와 비율은 모두 학습 조각에서만 만든다.
4. `hgb`: `log1p` CPU·RAM 요청, scheduling class, priority 네 특징으로 `log1p` 관측 최대의 90분위수를 학습한다. `HistGradientBoostingRegressor(loss='quantile', quantile=0.9, max_iter=150, max_leaf_nodes=15, max_depth=4, min_samples_leaf=20, learning_rate=0.05, l2_regularization=1.0, random_state=42)`를 고정한다. 예측은 `expm1` 후 0 이상으로 자른다.

비율 분위수와 HGB에는 각 학습 job의 task 가중치 총합이 같도록 `1 / job의 적격 task 수`를 사용한다. `conditional_ratio`의 한 축 bin과 4×4 셀 분위수에서는 **그 부분집합 안에서** job별 가중치 합이 다시 같도록 `1 / 해당 bin·셀 안의 job task 수`로 계산한다. 검증에서 하이퍼파라미터나 bin 수를 탐색하지 않는다.

## 선택, 점수, 해석

주 점수는 원래 정규화된 자원 단위에서 task별 pinball loss를 낸 뒤 job 안에서 평균하고, job 사이를 동일 가중으로 평균한 값이다. 부 점수는 task 행 균등 평균 pinball이다. 둘 다 낮을수록 좋다. q90 피복률도 같은 두 가중 방식으로 보고한다. 검증의 **job 균등 pinball**이 가장 낮은 후보를 CPU·RAM별로 선택한다. 정확한 동률이면 `request`, `global_ratio`, `conditional_ratio`, `hgb` 순서를 적용한다. 선택 결과, 코드와 원본 SHA-256, 학습 결과물 SHA-256을 manifest에 기록한 후에만 시험 `00400` 정답을 읽는다.

시험에서는 네 후보 모두 점수화하되, 검증에서 고른 후보를 주 결과로 제시한다. 시험의 사전 판정은 HGB가 **`00200` 검증 job 균등 pinball에서 선택된 비학습 기준선**(`request`, `global_ratio`, `conditional_ratio` 중 하나)보다 **CPU와 RAM 각각** 시험 job 균등 pinball을 5% 이상 줄이고, 시험 행 균등 pinball을 5% 넘게 악화시키지 않으며, 시험 job 균등 q90 피복률이 각 차원에서 0.85 이상인지이다. 어느 조건이라도 실패하면 learned model 개선을 확인하지 못한 것으로 기록한다. 2,000회 job 단위 bootstrap(고정 seed 2026)으로 HGB와 검증 선택 비학습 기준선의 job 균등 pinball 차이 95% 구간을 함께 보고한다. 작은 job 수, 큰 job 쏠림, 원래 배정·생존 조건, 사용량 구간 겹침의 한계를 명시한다.

이 결과는 범용 Linux task의 **기존 배정 아래 관측 사용량**을 예측할 수 있는지만 시험한다. AI 응용의 최소 안전 CPU·RAM, GPU·VM 형상, 사용자 지연 SLO, 배포·스케일링 효과의 정답으로 해석하지 않는다.
