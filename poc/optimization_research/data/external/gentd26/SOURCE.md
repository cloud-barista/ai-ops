# Alibaba GenTD26 데이터 출처와 사용 범위

- 원 출처: [Alibaba Cluster Trace, GenAI Serving Top-Down Dataset 2026](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-v2026-GenAI/README.md).
- 고정 원본 commit: `8c132fb7717fbd44cf434cf81f2cd865a80072d5` (2026-04-27).
- 다운로드·해시 검증: `python scripts/fetch_gentd26.py`. 압축 원본의 SHA256은 해당 스크립트에 고정했다. 로컬 다운로드 날짜는 2026-09-28이다.
- 데이터 인용: 원 출처 README의 SoCC '25 / EuroSys '26 인용 정보를 따른다.

다운로드한 파일은 `lora_request_trace.csv`(26,823개 요청), `qps.csv`, `queue_size_raw_anon.csv`, `queue_rt_raw_anon.csv`, `pod_gpu_duty_cycle_anon.csv`, `pod_gpu_memory_used_bytes_anon.csv`, `pipeline_inference_data_anon.csv`와 그 압축 파일이다. 전부 공개된 익명화 데이터이며 원본 SQLite에서 추출한 측정과 섞지 않는다.

후속 적재시간 연구를 위해 같은 고정 commit의 `pipeline_update_latency_anon.csv`, `basemodel_update_latency_anon.csv`, `lora_update_latency_anon.csv`, `controlnet_latency_data_anon.csv`, `model_predict_data_anon.csv`, `pod_memory_util_anon.csv`와 압축 파일을 추가했다. `fetch_gentd26_update.py` (`../../scripts/fetch_gentd26_update.py`)는 압축본 SHA256, 단일 CSV 멤버, 기존 파일 동일성을 확인한다. 파이프라인 적재 지연은 36,046행·132 익명 컨테이너·약 22.42시간이며 0ms가 2,622행이다. 원본 README는 이를 파이프라인 loading/switching latency(ms)라고 정의하지만 업데이트 명령 ID·발행 시각·종료 시각·모델 버전과 0ms 의미는 제공하지 않는다. 파일들의 같은 `(container_ip,timestamp_anon)` 키가 반드시 하나의 가산적 업데이트 사건을 뜻하지도 않는다. 같은 키의 6,920개 파이프라인·base 관측 중 파이프라인 값이 base보다 작은 건수가 39.25%다. 동시 구성요소 값을 사전 입력이나 정확한 지연 분해에 사용하지 않는다.

요청 CSV는 2024-11-15~2024-12-08의 이미지 생성 요청이다. QPS·대기시간·GPU 모니터링 CSV는 익명 시각 `1662858720`~`1662940800`의 약 22.8시간 구간이다. 두 구간의 시각이 서로 다르므로 동일 요청을 해당 모니터링 행에 연결하지 않는다. 모니터링 파일의 `container_ip`는 동일 구간 내에서만 공통 식별자로 사용한다. 연구 코드에서 생성하는 예측 대상은 요청 **실행시간** 또는 모니터링 값이며 실제 VM 배포·증설의 인과 결과가 아니다.

Google의 [2019 Borg 추적 자료](https://github.com/google/cluster-data/blob/master/ClusterData2019.md)는 2.4 TiB 압축·BigQuery 제공이며 CPU/메모리 자원 요청과 사용량에 초점을 둔다. [2011 자료](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)는 약 41 GB이며 2011년 CPU/메모리 작업 추적이다. 둘 다 현재 부족한 AI 요청 실행시간과 GPU 모니터링을 직접 보완하지 못하므로 이 실험에서는 GenTD26을 우선 사용한다. 더 큰 [Alibaba GPU v2026 추적 자료](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/README.md)는 GPU/CPU 요청과 사용량을 제공하지만 사용자 요청 지연은 없다. 전체 시간별 pod 압축 파일은 [약 352 GB](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md)다.
