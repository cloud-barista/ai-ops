# Alibaba MaaS 주간 5분 추적: 독립 인스턴스 ID bucket 2

2026-09-29. 출처는 [Alibaba MaaS 공식 설명](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/README.md)의 `instance_week_5min.zip`이다. [기존 전량 원본 감사](../alibaba_maas_2026/SOURCE.md)에 고정된 ZIP 크기·ETag와 21개 멤버별 CRC32/SHA-256을 다시 확인했다. 원본 **140,906,827행** 중 `SHA256(UTF-8 정규화 instance_id)` 첫 8바이트 정수 `%100 == 2`인 ID **46,753개·1,425,039행**을 추출했다. bucket 0·1과 원시 ID 해시가 겹치지 않음을 평가 로더에서 확인했다.

[새 ID 시험 규약](../../../reports/maas_state_day_calibration_new_id_protocol.md)과 코드·저장 모델의 [원본 접근 전 release manifest](../../../artifacts/maas_state_day_calibration_holdout_v1/release_manifest.json)를 확인한 뒤 추출했다. 원본 감사 JSON (`instance_week_5min_bucket2_audit.json`)은 21개 멤버의 재검증과 합본 해시를 기록한다. 합본 `instance_week_5min_bucket2.parquet`의 SHA-256은 `5a76e339c6ab5906f84b53f11cde4380dac7ff54d6fb335a77838851ec9e952e`다. 원본 ZIP과 임시 멤버는 보존하지 않았다.

원래 ID는 합본에 없다. 공개 익명 ID의 무염 SHA-256도 재연결될 수 있어 행별 표본과 예측은 대외 배포하지 않는다. 이 자료는 같은 공개 서비스·같은 주간에서 ID만 분리한 표본이며, 별도 시기·서비스·GPU 세대 또는 VM 조치·사용자 SLO의 검증 자료가 아니다.
