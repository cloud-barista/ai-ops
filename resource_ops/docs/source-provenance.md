# Resource Source Provenance

현재 테스트 ResourceSource:

- type: Prometheus HTTP API
- default base URL: `http://127.0.0.1:9090`
- query endpoint: `/api/v1/query`
- node facts: node_exporter
- GPU facts: NVIDIA DCGM exporter
- join key: `node_id`

기본 URL은 테스트 편의를 위한 임시값이며 `RESOURCE_PROMETHEUS_URL`로 교체할 수 있다. 운영 credential은 저장소에 기록하지 않는다.

Source adapter는 외부 metric과 label을 `resource.RawFact`로 변환한다. RawFact 이후 계약은 source-neutral이므로 향후 전달 API가 바뀌더라도 추천 알고리즘의 provenance는 ResourceQuery와 normalized Snapshot으로 추적한다.

Prometheus adapter는 반환된 각 sample의 timestamp를 query 평가 시각과 비교한다. 기본 `RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE=2m`보다 오래되거나 미래인 표본은 사용하지 않는다. 응답의 `observed_at`은 남은 표본에서 가장 오래된 실제 계측 시각으로 정한다. 향후 다른 Source adapter도 자체 표본 시각·가용 시각을 검증해야 하며, 조회 시각을 계측 시각으로 대입하지 않는다.
