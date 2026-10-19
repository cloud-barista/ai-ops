# SwissAI Qwen 전체 접두의 60분 재사용 학습: 지원량 단계 중단

2026-09-29. [점수 전 규약](swissai_qwen_prefix_cache_protocol.md)과 [구현 선택 기록](swissai_qwen_prefix_cache_implementation_frozen.md)을 고정한 뒤, 검증된 시간순 Qwen 입력 버킷 (`../data/external/swissai_serving_trace/qwen_buckets_sorted.parquet`)에서 **현재 전체 완전 블록 접두열이 미래 60분에 더 긴 요청의 시작 부분으로 다시 나오는지** 계산했다. 학습·검증의 양·음성 지지만 확인했으며, 모델 학습과 검증/시험 예측·캐시 적중 점수는 하지 않았다.

## 사전 게이트와 결과

규약은 학습·검증 각각 미래 재사용 양성과 음성이 **1만 건 이상**, 검증에서 날짜별 양·음성이 각각 **100건 이상인 날이 3일 이상**이어야 학습한다고 정했다. 같은 생성 시각의 요청은 서로의 미래가 아니며, 각 분할의 첫 60분은 준비에만 쓰고 끝 60분은 미래 창 미완료로 제외한다. 이번 적격 요청은 실제로 모두 그 내부 창에 있었으므로 따로 검열된 행은 0이다.

| 구간 | 정답 관측 요청 | 미래 재사용 양성 | 음성 | 양성 비율 |
| --- | ---: | ---: | ---: | ---: |
| 학습 | 730,692 | 140,868 | 589,824 | 19.28% |
| 검증 | 30,800 | **2,204** | 28,596 | **7.16%** |

검증의 날짜별 양성/음성은 8월 1일 `489/24,924`, 3일 `147/250`, 4일 `0/3`, 6일 `40/192`, 7일 `1,528/3,227`이다. 날짜별 100건 하한은 정확히 **3일**이 충족하지만, 검증 양성 **2,204건**이 사전 1만 건 하한보다 **7,796건 부족**하다. [고정 지원량 JSON](../artifacts/swissai_qwen_prefix_v1/support.json)의 SHA-256은 `cc09ac947cf4c01b4bf3dee7214c872b1452eed53c357aacec0984a1b2b075db`이다. 날짜별 ID 해시 오름차순 최대 5천 건을 고르는 학습 표본 인덱스는 레이블과 독립적으로 66,487건 산출했지만, 게이트가 닫혀 모델 적합에 사용하지 않았다.

**판정: 이 전체 접두 `Y60` 방법은 미채택.** `prepare`는 `qwen_training_support_gate_failed`로 종료했고 `model.joblib`, `manifest.json`, `validation.json`, `test.json`을 만들지 않았다. 그 결과 검증/시험 Brier·AP, 4,096블록 LRU/LFU 적중률, 학습 입장 정책 적중률은 **미계산**이다. 지원량 부족을 본 뒤 같은 규약의 양성 하한을 낮추거나 고정 시험을 열지 않는다.

## 자료 경계와 후속 연구

[원본 구조 감사](swissai_qwen_bucket_structure_findings.md)에서 중복 ID의 두 사본 864,254행과 완전 블록이 없는 3,405행을 제외했다. 검증 7일은 요청이 일부 날짜에 몰려 있고, 학습 대비 전체 접두 재사용 양성률도 `19.28%→7.16%`로 달라졌다. 이는 데이터 분포·정답 지원의 관찰이지 정책 성능이나 서비스 원인의 증거가 아니다. 중복 ID 발생 이유와 원본 `trace.jsonl`과 버킷 파일 사이 2행 차이도 아직 규명되지 않았다.

이 결과를 본 뒤 더 짧은 접두 또는 블록별 효용으로 정답을 다시 정의한다면 **사후 탐색 과제**로 분리하고, 새 규약을 먼저 고정해야 한다. 같은 SwissAI 서비스의 고정 시험을 완전히 독립적인 외부 검증으로 주장할 수 없으며, 실제 GPU KV 메모리·TTFT·사용자 SLO나 VM 배포·스케일 효과로도 읽을 수 없다. [LPC](https://papers.neurips.cc/paper_files/paper/2025/hash/414f642a1ea9350006669774cba9bcd4-Abstract-Conference.html), [Marconi](https://proceedings.mlsys.org/paper_files/paper/2025/hash/7c180af017258d239bac6248d1eb26ac-Abstract-Conference.html) 등은 학습형 접두 캐시를 이미 연구했으므로, 이 표본 게이트나 후속 탐색을 경희대 고유 알고리즘 성과라고 부르지 않는다.

재현: [평가기](../scripts/evaluate_swissai_qwen_prefix.py)의 `prepare`를 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 scripts/evaluate_swissai_qwen_prefix.py prepare`로 실행한다. 지원량 결과는 일회성 파일이어서 같은 경로를 덮어쓰지 않는다. 이번 실행에서는 종료코드 1이 **예정된 지원량 실패**를 뜻하며, 잠금 시험 레이블을 읽었다는 뜻이 아니다. 합성 테스트는 입력 감사·재사용 특징·캐시 재생·평가 게이트를 포함해 별도로 통과했다.
