# ETH LLM 서빙 기록 전량 감사와 사전 품질 게이트

## 판정

[ETH EASL의 공식 LLM Serving Trace](https://huggingface.co/datasets/eth-easl/swissai-serving-trace)의 `trace.jsonl` 16,329,237행을 전량 내려받아 해시를 확인하고 시각순으로 정렬했다. 이 데이터는 [OpenTela OSDI '26 연구](https://www.usenix.org/conference/osdi26/presentation/yao)가 공개한 독립 서비스 기록이며, GenTD26·BurstGPT와 다른 서비스다. 하지만 성능 점수를 보기 전에 고정한 [검증 규약](swissai_external_validation_protocol.md)의 **전체 요청 품질 게이트를 통과하지 못한다.** 2024-12-04~12-14의 완전한 10일 시험 블록에서 양수 종료시간은 203,009/215,742건(94.10%)으로 사전 문턱 95% 아래다. 고정 day 0–30 학습 기간에는 요청이 70건뿐이어서 필요한 양수 라벨 10,000건에도 못 미친다. 따라서 고정 규약에 따른 독립 재현성의 성공/실패 점수를 계산하지 않는다. 뒤 기간으로 시작점을 옮긴 실험은 별도 탐색 평가로만 표기해야 한다.

## 출처와 처리

고정 revision `c3dd66602b564ce189f7db81115620413e646cd5`의 원본 크기는 6,981,478,609 bytes, LFS SHA256은 `79c92fd40752e40a6cac1fc897491ea2f54f37939dc4dcc48c96ea06383c81b7`이며 전량 다운로드 후 일치했다. [`fetch_swissai_trace.py`](../scripts/fetch_swissai_trace.py)는 구간별 이어받기와 원본 해시 검증을 수행하고, [`audit_swissai_trace.py`](../scripts/audit_swissai_trace.py)는 모든 JSON 행을 다시 해시 검증한 뒤 `requests_sorted.parquet` (`../data/external/swissai_serving_trace/requests_sorted.parquet`)을 만든다. 축약 파일은 **196,786,207 bytes**, SHA256 `d5c2bf4cc038f936a9713f8fc7c0430703ddd1c03ed0518fb65aaf68e160997b`, 16,329,237행이다. 생성 코드 SHA256은 `d3ffb00641dae4046aaada8a04c2a9071b401791db3b940d153edf12d6a8046f`다. 자세한 수집·라이선스 경계는 [`SOURCE.md`](../data/external/swissai_serving_trace/SOURCE.md)에 있다.

축약 파일에는 요청/완료 UTC 마이크로초, 경과시간, 모델, 상태, **보고된** 입력 토큰, 원본 행 번호, 시각 문자열의 시간대 표시 여부만 남겼다. 요청 ID·원시 생성 파라미터·출력 토큰은 복사하지 않았다. `reported_input_tokens`의 요청 시점 가용성은 공식 카드가 보장하지 않아 예측 입력으로 쓰지 않는다. 원본 16,329,237행 모두 요청 시각을 파싱할 수 있었다. 원본 행에서는 이전 행보다 요청 시각이 앞서는 경우가 8,162,576번 있었으므로 행 번호로 학습·시험을 나누면 안 된다. 축약 파일은 `(created_utc_us, source_row)` 순서로 전량 정렬하고 행 수와 단조성을 검증했다.

모델 필드 1건에는 접근 키처럼 보이는 문자열이 있어 원본·축약 파일을 로컬에만 두고, 평가 JSON·학습 모델 메타데이터의 해당 값은 해시 별칭으로 가린다. 모델명이 사용자 입력 필드라는 점을 고려한 처리다.

## 라벨 품질과 시각 형식

UTC 가정에서 양수 종료시간은 16,294,134건(99.785%), 완료시각 결측 `\\N`은 12,898건, 0초는 22,205건, 음수는 0건이다. `status=DEFAULT`에서도 완료시각이 없는 12,898건이 있어 상태만으로 라벨 존재를 판단할 수 없다.

| 상태 | 전체 요청 | 양수 종료시간 | 비율 | 완료시각 결측 | 0초 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `DEFAULT` | 13,878,069 | 13,863,573 | 99.896% | 12,898 | 1,598 |
| `ERROR` | 2,451,168 | 2,430,561 | 99.159% | 0 | 20,607 |

전체 16,329,237건의 `created_at`에는 명시적 UTC가 있다. `finished_at`은 명시적 UTC 4,751,687건, 시간대 없는 문자열 11,564,652건, 결측 12,898건이었다. UTC로 해석하면 시간대 없는 완료시각 중 11,542,447건이 양수, 22,205건이 0초다. 시간대 없는 종료시각을 UTC+1 현지시각으로 재해석하면 그중 양수는 **66건**, UTC+2로 재해석하면 **48건**만 남고 나머지는 거의 전부 음수가 된다. 따라서 UTC 가정이 관측된 시각 관계에 가장 잘 맞지만, 저자의 시간대 명시는 찾지 못했으므로 가정으로 유지한다.

2024-07-17~2025-10-23의 464개 달력일 중 요청이 있는 날은 362일이다. 고정 규약의 day 70–80(2024-09-25~10-05)은 0건이다. [공식 데이터 카드](https://huggingface.co/datasets/eth-easl/swissai-serving-trace/blob/c3dd66602b564ce189f7db81115620413e646cd5/README.md)와 [논문 §6.1](https://www.usenix.org/system/files/osdi26-yao.pdf)은 추적 범위를 설명하지만 이 구간이 왜 비었는지는 설명하지 않는다. 이를 실제 서비스 요청 0건이나 수집 장애라고 단정할 수 없다.

고정 학습 day 0–30(2024-07-17부터)은 70건이고 종료 후 추가 1시간까지 경계 전에 가용한 라벨도 70건이다. day 30–40의 첫 검증 구간은 요청 89건 중 블록 시작에 가용한 양수 라벨이 74건이다. day 140–150에서 양수 라벨을 잃은 12,733건 중 모델 문자열 `\\N`의 6,702건은 완료시각이 없고, `meta-llama/Meta-Llama-3.1-70B-Instruct`의 4,418건은 완료시각이 없으며 1,604건은 0초다. 시험 전체에서 1,000건 이상인 모델 `\\N`은 6,740건 중 양수 라벨 35건(0.52%)으로 모델별 95% 문턱도 통과하지 못한다. 이 문자열은 모델 미상 표시로 보아야 하며 실모델 성능으로 해석하지 않는다.

## 재현과 경계

```bash
python scripts/fetch_swissai_trace.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/audit_swissai_trace.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_swissai_trace_ingestion.py' -v
```

원본 전체 집계는 `audit.json` (`../data/external/swissai_serving_trace/audit.json`)에 있으며 상태·모델·날짜·모델×날짜·시각 형식별 건수를 포함한다. 합성 시각·결측·전역 정렬 검사 2개가 통과했다. 공식 카드의 라이선스 표시는 `other`로, 원본과 행 단위 축약 파일은 로컬 연구에만 보관하고 재배포하지 않는다. 이 자료에도 VM/GPU 형상이나 실제 증설 명령은 없어 Scale-out/in 효과를 학습할 정답은 없다.
