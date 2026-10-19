# 등록 정보 기반 관측 GPU 예약 피크의 상한 예측

## 결론

기존 SQLite에서 GPU 1개·CPU affinity 2개의 **실측 PyTorch 예약 메모리 피크**를 대상으로, 모델 크기와 요청 조건을 입력으로 받는 Ridge 모델을 학습하고 donor 모델의 교차검증 잔차로 예측값을 보정했다. 모델 하나씩 제외한 9모델 평가에서 비율 보정 상한의 주 캠페인 모델 평균 커버리지는 `88%`, 평균 초과 배정량은 `146 MiB`, 90% 분위수 손실은 `63.8 MiB`였다. 같은 자료의 상수 오버헤드 상한은 `88% / 225 MiB / 73.3 MiB`, 보정하지 않은 강한 Ridge 점 예측은 `49% / 17 MiB / 97.6 MiB`였다. 따라서 보정은 관측 피크의 과소예측을 줄이지만 **미지 모델에 90% 커버리지를 보장하지 않는다**.

실패 사례가 중요하다. 유일한 생성형 모델 Qwen을 통째로 제외하면 donor에는 같은 `workload_task`가 없고, Qwen 주 캠페인 커버리지는 `27%`, 최대 부족량은 `2,380 MiB`였다. 모델 제외 시 같은 작업 종류의 donor 모델이 둘 미만이면 예측을 보류하도록 했다. 이 규칙으로 평가 가능한 encoder 8모델만 보면 주/새 조건/공간표본 캠페인의 모델 평균 커버리지는 각각 `95%/94%/95%`다. 그러나 BERT 계열 4~5모델을 한꺼번에 제외하면 커버리지는 `100%`인 대신 큰 과배정이 나타난다. 관측 피크 상한을 그대로 GPU 용량 또는 OOM 안전 판정에 쓰지 않는다.

이 연구는 경희대의 **응용 요구사항 분석·인프라 형상 추천을 위한 메모리 신호**를 학습한 것이다. 기존 [VRAM 점 예측 실험](vram_peak_findings.md)은 평균 오차가 낮아도 과소예측이 많았다. 여기서는 상한의 커버리지와 낭비를 함께 검증한다. 자료와 후보 방식을 본 뒤 만든 탐색 실험이며 학술적 최초성이나 독립 검증을 주장하지 않는다.

## 자료와 예측 시점

- 고정 SQLite 추출 자료 (`../data/audit_v1/measurements.parquet`)에서 성공·eligible 실행, `cuda`, replica 1개, CPU affinity 2개를 선택했다. 로컬 주 캠페인 `575`행·9모델, 새 입력 조건 `18`행, 공간표본 조건 `44`행이다. 학습과 잔차 보정에는 주 캠페인만 쓴다.
- 정답은 `gpu_memory_reserved_bytes / 2³⁰` GiB다. 이는 `torch.cuda.max_memory_reserved()` 관측값으로, [PyTorch의 CUDA 메모리 설명](https://docs.pytorch.org/docs/stable/notes/cuda.html)에서 구분하는 캐싱 할당자의 예약량이다. 최소 실행 가능 VRAM, OOM 발생 가능성 또는 다른 GPU의 메모리 사용량을 뜻하지 않는다. 기존 [메모리 적합성 감사](memory_feasibility_review.md)에서 확인했듯 원본에는 OOM 사례가 없다.
- 추론 입력은 등록 시 확보했다고 **가정한** `model_parameter_bytes`, 요청 batch size·최대 길이·동시성·`workload_task`다. 자료에서는 파라미터 바이트와 작업 종류가 실행 후 `result_json`에 기록되어 있다. 실제 서비스가 동일한 로딩 방식과 dtype의 등록 메타데이터를 실행 전에 제공해야 이 인터페이스가 성립한다. 실행 후 피크·모니터링 값은 입력에 넣지 않았다.
- 평가에서는 대상 모델 ID 전체를 학습과 잔차 보정에서 제거하고, 대상 모델의 주·새 조건·공간표본 행을 예측했다. 추가로 BERT 4종, BERT 4종과 DistilBERT를 함께 제외했다. 새 조건·공간표본 캠페인은 주 캠페인보다 **먼저 수집**됐으므로 시간상 미래 검증이 아닌 입력 조건 홀드아웃이다. 이 두 캠페인의 결과를 자동 학습·잔차 보정에는 넣지 않았지만, 보정 후보와 보류 규칙은 같은 SQLite의 파일럿을 본 뒤 정했다.

## 학습 알고리즘과 비교군

`P`를 등록 모델 파라미터 GiB, `x`를 모델 크기·batch·길이·동시성·작업 종류의 정적 특징, `y`를 관측 피크라고 하자. 같은 특징으로 `log(y)`를 예측하는 `StandardScaler + Ridge(alpha=10)`을 주 캠페인 donor에 학습한다. 직접 예측은 `m(x)=max(P, exp(f(x)))`로 제한한다. 각 donor 모델을 한 번씩 제외하고 다시 학습하여 donor 실행의 교차검증 비율 `r=y/m_{-model}(x)`을 구한다. 모델마다 총 가중치가 같도록 `r`의 경험적 90% 분위수 `q`를 구하고, 최종 상한을 `U(x)=max(1,q)·m(x)`로 출력한다. 전체 주 캠페인 9모델로 학습한 연구용 모델 (`../artifacts/vram_upper_v1/research_estimator.joblib`)의 보정 계수는 `1.468758`이다.

동일한 donor 분할로 네 비교군을 평가했다.

1. `ridge_point_floor`: 위 Ridge 점 예측. 커버리지를 높이기 위한 보정은 없다.
2. `donor_overhead_q90`: `P + donor (y−P)`의 모델 균등 가중 90% 분위수. 특징에 따른 오버헤드 회귀가 없는 상한이다.
3. `ridge_additive_oof_q90`: Ridge 점 예측에 모델 제외 donor 잔차 `y−m_{-model}(x)`의 90% 분위수를 더한다.
4. `overhead_scaled_oof_q90`: 앞선 실험의 `P+exp(g(x))`를 점 예측으로 두고, 모델 제외 donor의 실제/예측 오버헤드 비율의 90% 분위수를 곱한다.

교차검증 잔차의 분위수는 **경험적 휴리스틱**이다. donor 실행들이 모델별로 묶여 있고, 새로운 모델이 donor와 교환가능하다고 입증되지 않았다. [Romano·Patterson·Candès의 conformalized quantile regression](https://arxiv.org/abs/1905.03222)에서 말하는 유한표본 커버리지 보장을 이 구현에 적용하지 않는다. 보정 후보와 작업 종류별 보류 규칙은 파일럿 결과를 보고 정했으므로 아래 결과는 탐색적이다.

## 모델 하나씩 제외한 결과

표의 커버리지·평균 초과 배정량·분위수 손실은 **모델별 점수를 구한 뒤 각 모델에 같은 가중치**를 준 수치다. 초과 배정은 `max(예측−실측, 0)`의 평균이다. 90% 분위수 손실은 부족량을 0.9, 초과량을 0.1 가중한다. 낮을수록 좋지만 커버리지 목표와 함께 봐야 한다.

| 방법 | 주: 커버리지 / 초과 MiB / 손실 MiB | 새 조건: 커버리지 / 초과 / 손실 | 공간표본: 커버리지 / 초과 / 손실 |
| --- | ---: | ---: | ---: |
| 보정 없는 Ridge | `49% / 17 / 97.6` | `39% / 8 / 71.8` | `53% / 21 / 61.1` |
| donor 오버헤드 90% | `88% / 225 / 73.3` | `72% / 178 / 38.3` | `83% / 199 / 37.2` |
| Ridge 가산 잔차 90% | `88% / 252 / 88.0` | `83% / 228 / 47.9` | `87% / 258 / 54.1` |
| 학습 오버헤드 비율 90% | `85% / 160 / 65.6` | `83% / 179 / 37.9` | `84% / 203 / 40.7` |
| **Ridge 비율 잔차 90%** | **`88% / 146 / 63.8`** | **`89% / 145 / 31.5`** | **`90% / 176 / 36.4`** |

학습 작업 종류가 둘 이상의 donor 모델에서 관측된 경우만 대상으로 하면 Ridge 비율 보정의 주/새 조건/공간표본 결과는 `95% / 162 MiB / 20.9 MiB`, `94% / 161 MiB / 31.8 MiB`, `95% / 194 MiB / 29.2 MiB`다. 9모델 중 Qwen 1개를 제외한 **8모델**의 결과이며, 보류된 Qwen의 심각한 실패를 이 수치가 해결한 것은 아니다. BERT 계열 전체를 제외해도 donor에는 encoder 모델이 남으므로 보류되지 않는다.

## 계열 전이 실패와 채택 범위

| 평가 | Ridge 비율 상한 커버리지 | 평균 초과 MiB | 90% 분위수 손실 MiB | 보정 없는 Ridge 손실 MiB |
| --- | ---: | ---: | ---: | ---: |
| BERT 4종 공동 제외 · 주 | `100%` | `293` | `29.3` | **`14.0`** |
| BERT 4종 공동 제외 · 새 조건 / 공간표본 | `100% / 100%` | `343 / 361` | `34.3 / 36.1` | **`10.9 / 6.7`** |
| BERT 4종+DistilBERT 공동 제외 · 주 | `100%` | `372` | `37.2` | **`13.0`** |
| BERT 4종+DistilBERT 공동 제외 · 새 조건 / 공간표본 | `100% / 100%` | `434 / 457` | `43.4 / 45.7` | **`7.9 / 7.5`** |

가장 큰 전이 실패는 Qwen의 커버리지 `27%`와 최대 부족 `2,380 MiB`다. 보류 규칙은 donor의 작업 종류 모델 수가 2개 미만이면 숫자를 제공하지 않는다. 그래도 지원되는 encoder 안에서 BERT 계열 공동 제외 결과처럼 높은 커버리지가 유용한 상한을 뜻하지는 않는다. 특히 새 조건에서는 대상 계열의 피크가 donor보다 낮아 보정 상한이 과도하다. **현재 모델은 연구용 관측 피크 예측기로만 보존**하며, VRAM 형상 자동 추천·OOM 안전 판정·운영 배포에는 연결하지 않는다. 다른 GPU·호스트와 모델군을 검증할 자료도 없다.

## 재현 산출물

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_vram_upper.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_vram_upper.py' -v
```

[실험 코드](../scripts/evaluate_vram_upper.py), [누출·보류·직렬화 검사](../tests/test_vram_upper.py), 행별 예측 (`../artifacts/vram_upper_v1/forecasts.csv`), donor 보정 계수 (`../artifacts/vram_upper_v1/calibration.csv`), 전체 모델 제외 요약 (`../artifacts/vram_upper_v1/loomo_summary.csv`), 지원 작업만의 요약 (`../artifacts/vram_upper_v1/supported_loomo_summary.csv`), 계열 공동 제외 요약 (`../artifacts/vram_upper_v1/aggregate_metrics.csv`), 모델별 수치 (`../artifacts/vram_upper_v1/per_model_metrics.csv`), 전체 주 캠페인 학습기 (`../artifacts/vram_upper_v1/research_estimator.joblib`), [스냅샷·코드·모델 SHA와 feature schema](../artifacts/vram_upper_v1/manifest.json)를 남겼다. `joblib` 학습기는 같은 Python 의존성 환경에서 `forecast_with_research_estimator`로 사용할 수 있고, 학습 작업 종류가 2모델 미만이거나 호스트·GPU·CPU 프로필이 다르면 예측을 거부한다. 학습기 파일은 현지 실험용이며 사용자 입력으로 받은 역직렬화 파일을 읽도록 설계하지 않았다.
