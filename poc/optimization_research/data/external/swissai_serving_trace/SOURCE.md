# ETH LLM 서빙 기록의 출처와 가공 규칙

- 배포처: [ETH EASL의 SwissAI serving trace 데이터 카드](https://huggingface.co/datasets/eth-easl/swissai-serving-trace). 이 자료는 [OpenTela 연구](https://www.usenix.org/conference/osdi26/presentation/yao)에 연결돼 있다.
- 고정 revision: `c3dd66602b564ce189f7db81115620413e646cd5`. 원본 파일 [`trace.jsonl`](https://huggingface.co/datasets/eth-easl/swissai-serving-trace/blob/c3dd66602b564ce189f7db81115620413e646cd5/trace.jsonl)은 6,981,478,609 bytes이며 LFS SHA256은 `79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7`이다. 원본을 2026-09-28에 내려받아 전량 해시를 검증했다.
- 수집: `python scripts/fetch_swissai_trace.py`. 64MiB HTTP Range 단위로 이어받으며 모든 구간을 받은 뒤 LFS SHA256을 확인한다.
- 감사·축약: `python scripts/audit_swissai_trace.py`. 전체 원본을 다시 해시 검증하고, 요청 ID·원시 생성 파라미터·출력 토큰을 복사하지 않은 `requests_sorted.parquet` (`requests_sorted.parquet`)을 만든다. 열은 요청/완료 UTC 마이크로초, 경과시간, 모델, 결과 상태, 보고된 입력 토큰, 원본 행 번호와 원시 시각의 시간대 표시 여부다. 정렬 키는 `(created_utc_us, source_row)`다. `audit.json` (`audit.json`)에 전체 품질 집계가 있다.
- 저자 카드의 라이선스 메타데이터는 `other`이고 별도 라이선스 텍스트가 공개 파일 목록에 없었다. 원본과 행 단위 축약 파일은 로컬 연구용으로만 보관하며 재배포하지 않는다.

원본에는 `finished_at`이 명시 UTC(`Z`)인 행과 시간대 없는 행이 섞여 있다. 시간대 없는 값은 일단 UTC로 해석하고 `created_naive`/`finished_naive` 플래그를 남겨 UTC+1·UTC+2 재해석을 별도로 검토한다. `finished_at='\\N'`은 완료시간 결측이다. 자료는 원본 행 순서가 시계열 순서가 아니므로 반드시 전역 정렬 파일로 시간순 분할한다. `status`, 완료시각, 출력 토큰은 요청 시점 입력이 아니고 `reported_token_input`의 실제 요청 시점 가용성도 카드만으로 확인되지 않는다. 이 자료만으로는 VM 형상 또는 Scale-out/in 효과를 관측할 수 없다.

## Local storage compression
- The pinned source is stored locally as `trace.jsonl.gz` using gzip level 1. The archive is 1083122851 bytes, compressed SHA-256 `bdcea83c88bc7a40e0128867f3ef063a37ea24ab27ab26395126cdaacd26aca0`.
- Decompressing the archive reproduces the upstream `6981478609` bytes and SHA-256 `79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7` exactly. The SHA in the audit remains the uncompressed source SHA.
- SwissAI ingestion, audit, demand checks, and output-token extraction accept either the raw JSONL or this gzip archive.
