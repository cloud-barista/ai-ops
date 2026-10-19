# 등록 모델 크기와 작업 조건으로 관측 GPU 예약 피크 예측

## 판정

현재 SQLite에서 **같은 호스트·GPU에서 실행했을 때 관측된 GPU당 PyTorch 예약 메모리 피크**는 학습할 수 있다. 등록 시 모델 파라미터 바이트 수를 알고 있다고 가정하고, 이를 피크의 바닥값으로 분리한 회귀는 모델 하나씩 제외한 주 캠페인에서 강한 직접 Ridge보다 평균 절대 로그오차(MALE)가 `0.2068→0.1502`로 낮았다. 그러나 BERT 네 모델을 함께 제외하면 주 캠페인은 사실상 동률(`0.1746` 대 `0.1747`)이고, 새 입력 조건에서는 분리 방식이 더 나빴다. 따라서 **탐색적인 관측 피크 추정 방법**으로 남긴다. 이를 안전한 VRAM 상한, 최소 GPU 메모리, 특정 VM/GPU에서의 실행 가능성 또는 배포 추천기로 채택하지 않는다.

이 연구는 앞선 [메모리 적합성 감사](memory_feasibility_review.md)가 구분한 두 대상 중 *관측 피크 회귀*만 다룬다. 원본에서 OOM은 0건이고 성공 실행의 피크는 장치 용량보다 훨씬 낮으므로, 실제 메모리 한도를 줄였을 때의 성공·실패를 검증할 수 없다. 이 방법은 단일 호스트의 관측값 예측이며 학술적 최초성이나 독립 검증 결과도 주장하지 않는다. 제안과 feature는 초기 파일럿 결과를 본 뒤 확정했으므로 사전등록 실험이 아니다.

## 자료·결정 시점

- 입력: 고정 SQLite 추출 스냅샷 (`../data/audit_v1/measurements.parquet`)의 `success=1`, `eligible=1`, `cuda`, GPU replica 1개, CPU affinity 2개. 로컬 주 캠페인 575행(encoder 8모델 각 64조건, Qwen 63조건), novel-matrix 18행(모델별 2개), space-filling 44행(encoder 모델별 5개, Qwen 4개)이다.
- 정답: `gpu_memory_reserved_bytes / 2³⁰` GiB. 값은 각 실행의 `torch.cuda.max_memory_reserved()` 피크다. [PyTorch 문서](https://docs.pytorch.org/docs/stable/notes/cuda.html)는 예약 메모리가 캐싱 할당자가 관리하는 양이며 현재 텐서가 쓰는 할당량과 다르다고 구분한다. 이 연구는 GPU1만 사용하므로 다중 replica 합산 메모리를 단일 GPU 피크로 잘못 해석하지 않는다. GPU2/다른 호스트에 대한 성능은 검증하지 않았다.
- 사전 입력: 모델의 **등록 시 별도로 확보한** `model_parameter_bytes`, 요청 batch size·최대 길이·동시성·작업 종류. 고정 자료에서는 파라미터 바이트와 작업 종류가 실행 후 `result_json`에 기록돼 있으나, 파라미터 바이트는 3개 캠페인에서 모델별로 일정함을 확인했다. 실제 사용에서는 같은 dtype·로딩 방식에 해당하는 바이트 수와 작업 종류를 등록 정보에서 실행 전에 얻어야 한다. 등록 정보가 없으면 현재 제시한 사전 예측 인터페이스도 성립하지 않는다.
- 입력 특징: `log(parameter GiB)`, `log(batch)`, `log(length)`, `log1p(concurrency)`, causal-LM 여부, `log(batch)×log(length)`, `log(parameter GiB)×log(batch)×log(length)`. 예측할 실행의 예약 피크·모니터링 값·사후 상태는 입력에 넣지 않는다.

모델 ID를 하나씩 통째로 제외하고 남은 **주 캠페인 donor**로만 학습했다. 제외 모델의 주 캠페인·novel·space 행은 평가에만 썼다. BERT 4종 공동 제외와 BERT 4종+DistilBERT 공동 제외도 별도 평가했다. 새 조건에 맞춘 재학습·측정은 없다. novel·space는 주 캠페인보다 먼저 수집돼 이 비교는 **시간상 미래 검증이 아닌 입력 조건 홀드아웃**이다. 표의 평균은 각 모델의 MALE 또는 MiB 오차를 먼저 구한 뒤 모델에 동일 가중치를 준 값이다.

## 방법과 강한 비교군

제안식은 `예상 피크 GiB = 등록 파라미터 GiB + exp(f(x))`다. `f`는 donor 실행의 `log(관측 피크 − 등록 파라미터 크기)`를 예측하는 StandardScaler+Ridge(`alpha=10`)다. 양수 오버헤드가 모든 선택 행에서 성립하는지 코드가 검사한다. 이 바닥값은 파라미터 바이트를 실제 VRAM 점유의 정확한 법칙이라고 주장하는 것이 아니라, 이 자료에서 관측한 구조를 활용한 가정이다.

비교군은 (1) 등록 파라미터 크기+donor 중간 오버헤드, (2) 같은 정적 특징으로 전체 `log(피크)`를 직접 Ridge 회귀, (3) 동일 특징으로 직접 ExtraTrees(`200`개, 최소 잎 `2`) 회귀다. 직접 회귀가 파라미터 크기보다 작게 예측하는 경우를 막은 **바닥값 적용 버전**도 함께 평가했다. 모델 선택·하이퍼파라미터를 대상 모델이나 외부 캠페인 결과로 조정하지 않았다.

## 모델 하나씩 제외한 결과

| 방법 | 주 캠페인 MALE / 절대 MiB | Novel MALE / 절대 MiB | Space MALE / 절대 MiB |
| --- | ---: | ---: | ---: |
| 등록 크기+donor 중간 오버헤드 | `0.2158 / 119.9` | `0.3289 / 129.3` | `0.2618 / 104.0` |
| 직접 로그 Ridge, 파라미터 바닥값 적용 | `0.2068 / 123.8` | `0.1809 / 86.9` | `0.1997 / 86.8` |
| 직접 로그 ExtraTrees, 파라미터 바닥값 적용 | `0.2209 / 126.7` | `0.2817 / 114.4` | `0.2831 / 107.7` |
| **파라미터+학습한 로그 오버헤드** | **`0.1502 / 88.5`** | **`0.1706 / 64.7`** | **`0.1777 / 62.5`** |

직접 Ridge에 바닥값을 적용하지 않으면 주/novel/space MALE은 `0.2254/0.2056/0.2190`, 직접 ExtraTrees는 `0.2636/0.3288/0.3293`이다. 직접 Ridge의 주 캠페인 575개 예측 중 70개가 알려진 파라미터 크기보다 작았다. 바닥값 비교를 넣어도 분리 방식이 주 캠페인에서는 9모델 중 8모델에서 MALE을 줄였다. 하지만 Qwen 한 모델의 주 캠페인 개선 폭이 크고(바닥값 직접 Ridge `0.491→0.322`), Albert는 오히려 악화된다. 모델별 수치와 예측 행은 아래 산출물에 모두 남겼다.

바닥값 직접 Ridge 대비 분리 방식의 모델별 MALE 차이는 주 `−0.0565`, novel `−0.0102`, space `−0.0220`이다. 모델 9개를 단위로 쌍체 재표본추출한 기술적 95% 구간은 각각 `[−0.0940, −0.0221]`, `[−0.0737, 0.0659]`, `[−0.0777, 0.0480]`이다(고정 seed 42, 50,000회). 모델 수가 적고 주제를 파일럿 결과 후 선택했으므로 확증적인 신뢰구간으로 해석할 수 없다. 외부 캠페인은 모델별 2~5조건에 불과하다.

## 계열 공동 제외와 과소추정

| 제외 계열·캠페인 | 분리 방식 MALE | 바닥값 직접 Ridge MALE | 해석 |
| --- | ---: | ---: | --- |
| BERT 4종 · 주 | `0.1747` | `0.1746` | 동률 |
| BERT 4종 · novel / space | `0.2310 / 0.2651` | `0.1830 / 0.2258` | 두 새 조건에서 악화 |
| BERT 4종+DistilBERT · 주 | `0.1599` | `0.1771` | 개선 |
| BERT 4종+DistilBERT · novel / space | `0.1899 / 0.2340` | `0.1680 / 0.2297` | 두 새 조건에서 악화 |

분리 방식의 **점 예측**은 실측 피크를 주 캠페인 `267/575=46.4%`, novel `12/18=66.7%`, space `15/44=34.1%`에서 과소추정한다. 모델별 평균 과소추정 비율은 주 `46.5%`, novel `66.7%`, space `35.6%`다. 주 캠페인의 모델별 평균 절대오차는 `88.5 MiB`지만, Qwen 조건의 최대 부족량은 `2,530 MiB`였다. 모델별 최대 부족량은 집계 파일 (`../artifacts/vram_peak_v1/aggregate_metrics.csv`)에 있다. 따라서 이 점 추정치를 그대로 VRAM 요구량으로 출력하면 안 된다. 여기서는 donor-only 상위 분위수 보정의 미래 커버리지도 검증하지 않았다.

## 재현과 해석 범위

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_vram_peak.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_vram_peak.py' -v
```

[실험 코드](../scripts/evaluate_vram_peak.py), [누출·물리범위 검사](../tests/test_vram_peak.py), 행별 예측 (`../artifacts/vram_peak_v1/predictions.csv`), 모델별 점수 (`../artifacts/vram_peak_v1/per_model_metrics.csv`), 전체 점수 (`../artifacts/vram_peak_v1/aggregate_metrics.csv`), 모델 단위 탐색적 재표본추출 (`../artifacts/vram_peak_v1/paired_model_bootstrap.csv`), [스냅샷·코드 해시와 설정](../artifacts/vram_peak_v1/manifest.json)을 남겼다. 테스트는 대상 모델·외부 캠페인의 피크값과 `result_json` 속 피크를 바꿔도 예측이 같음을 확인한다. 같은 호스트·같은 GPU만 있는 현재 자료로는 관측 피크의 새 하드웨어 전이, 최소 VRAM 또는 OOM 위험을 판정할 수 없다.
