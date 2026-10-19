# Resource Recommendation Assumptions

1. `node_id`는 한 Snapshot 안에서 리소스를 유일하게 식별한다.
2. Prometheus `instance`는 exporter마다 포트가 달라 join key로 사용하지 않는다.
3. CPU logical core는 `node_cpu_seconds_total`의 고유 CPU label 개수다.
4. CPU 가용 core는 최근 5분 idle 비율을 총 logical core에 적용한 추정값이다.
5. 메모리 가용량은 `node_memory_MemAvailable_bytes`다.
6. 초기 스토리지 범위는 root filesystem이다.
7. DCGM framebuffer metric 단위는 MiB다.
8. 장치 VRAM 총량은 free + used + reserved로 계산한다.
9. ResourceRecommendation은 point-in-time advisory이며 예약이나 할당 보장이 아니다.
10. 필수 가용 metric이 누락되면 0으로 대체하지 않고 해당 후보를 제외한다.
11. Prometheus는 임시 Source이며 향후 전달 소스가 바뀔 수 있다.
12. 실제 배포 직전 용량 재검증은 downstream 배포 계층 책임이다.
13. Prometheus query 시각은 계측 시각이 아니다. 기본 2분보다 오래되거나 query 시각보다 미래인 표본은 버리고, 응답 `observed_at`에는 사용한 표본 중 가장 오래된 시각을 쓴다. 최대 허용 나이는 `RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE`로 조정한다.
