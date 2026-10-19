# SwissAI Qwen 버킷 원본·학습 지원도 감사

2026-09-29. 이 문서는 [점수 전 고정 규약](swissai_qwen_prefix_cache_protocol.md)에 따라 원본 구조와 시간 분할만 검사한 결과다. **미래 60분 접두 재사용 레이블(`Y60`), 검증·시험 예측 점수, 캐시 적중률은 계산하지 않았다.**

## 원본 무결성과 적격 표본

[SwissAI 공식 데이터 카드](https://huggingface.co/datasets/eth-easl/swissai-serving-trace/blob/c3dd66602b564ce189f7db81115620413e646cd5/README.md)의 `qwen3-32b-buckets.jsonl`을 gzip으로 저장해 해제 스트림의 **4,617,817,342바이트·3,994,435행·SHA-256 `58b2265b98a10ec3bb2a0b455c520731c1296967906c7adc7b6d3710a013e402`**를 확인했다. gzip은 981,187,386바이트다. 전 행의 `model`은 `Qwen/Qwen3-32B`, `status`는 `DEFAULT`였으며 생성 시각은 전 행 파싱됐다. 생성 시각 1,094,706건은 시간대 표기가 없어 규약대로 UTC로 해석했다. `DEFAULT`는 추적 자료의 기록 상태일 뿐 실제 응용 성공이나 SLO 충족을 증명하지 않는다.

요청 ID는 **432,127종이 각각 두 번** 등장했다. 중복 ID의 양쪽 사본을 모두 제외하는 규약 때문에 총 864,254개 원본 행이 해당된다. 또 3,405행은 완전한 16토큰 블록이 없어 제외했다. 두 제외 사유가 일부 겹치므로 최종 시간순 적격 행은 **3,127,766건**이다. 구조 적격 3,991,030행에서 중복 때문에 실제로 제거된 구조 적격 행은 863,264건이다. 나머지 버킷 길이 불일치, 잘못된 ID·시각·모델·JSON은 감사에서 관찰되지 않았다. 원본 파일 위치의 생성 시각 역전은 1,996,934회였으므로 파일 행 순서로 학습·검증을 나누면 안 된다.

로컬 압축 Parquet는 완전 블록 버킷만 담고 `(created_utc_us, SHA256(id))` 순으로 정렬했다. 원시 ID, 출력토큰, 완료시각, 사전 계산된 재사용 필드, 미래 레이블은 담지 않았다. 접두 고유성은 완전 블록 ID 배열의 SHA-256으로 세었다. 이 해시는 원문 프롬프트 동일성을 증명하지 않는다.

## 고정 시간 분할의 지원도

| 구간 | 적격 요청 | 서로 다른 전체 완전 블록 접두 | 준비 60분·미래 60분 제외 후 완전 관찰창 보유 요청 | 동시 생성시각 묶음 |
| --- | ---: | ---: | ---: | ---: |
| 학습 | 730,692 | 523,748 | 730,692 | 13,348 |
| 검증 | 30,800 | 28,653 | 30,800 | 1,232 |
| 잠금 시험 | 2,364,502 | 1,307,661 | 2,364,498 | 75,560 |
| 후행 보조 | 1,772 | 1,357 | 1,740 | 104 |

검증 7일의 적격 요청은 8월 1일 **25,413건**, 3일 **397건**, 4일 **3건**, 6일 **232건**, 7일 **4,755건**이다. 2일과 5일은 0건이다. 따라서 사전에 고정한 “양성·음성 각각 100건 이상인 검증 날짜가 3일 이상” 게이트는 미래 재사용 레이블을 계산하기 전에는 통과 여부를 알 수 없고, 날짜별 자료가 매우 불균형하다. 학습과 잠금 시험의 요청량도 크게 달라 **검증을 거치지 않은 시험 점수를 연구 결론으로 사용할 수 없다.**

이 감사만으로는 중복 ID가 생긴 원인, 기존 `trace.jsonl`의 `DEFAULT` Qwen 건수와 버킷 파일 행수의 2건 차이, 60분 접두 재사용 양·음성의 충분성, 예측 개선 또는 실제 GPU/TTFT 절감 여부를 알 수 없다. 원본 ID 대조와 `Y60` 지원 게이트는 별도 단계이며, 시험 레이블·캐시 결과는 고정된 검증 문턱을 통과하기 전까지 잠가 둔다.

## 재현 자료

- 감사 코드: [`audit_swissai_qwen_buckets.py`](../scripts/audit_swissai_qwen_buckets.py), SHA-256 `896a3bc73ec9615141d1b82c7af86da9493899f8002a774c10c99b165eaa4e05`.
- 원본 통계: `qwen_buckets_quality.json` (`../data/external/swissai_serving_trace/qwen_buckets_quality.json`), SHA-256 `e530b1b74cfb4076e34962dbfd81414fb8d0a76de0a73f1c39a8b341c7d727dc`.
- 로컬 적격 표: `qwen_buckets_sorted.parquet` (`../data/external/swissai_serving_trace/qwen_buckets_sorted.parquet`), 495,139,367바이트, SHA-256 `b50c10e3034c288332762d67c8fde09989d31ba10678dd2be97fd6deb20f0d7b`.
- 고정 규약: [`swissai_qwen_prefix_cache_protocol.md`](swissai_qwen_prefix_cache_protocol.md), SHA-256 `399dabeca72b196e2195953fe25a531fe71477dfa7ab8759a4bd8a1fd00dbbfb`.
- 합성 검사: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m pytest -q tests/test_audit_swissai_qwen_buckets.py` → **6 passed**.
