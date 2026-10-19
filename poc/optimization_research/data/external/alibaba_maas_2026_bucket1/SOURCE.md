# Alibaba MaaS 주간 5분 추적: 독립 인스턴스 ID bucket 1

2026-09-29. 출처는 [Alibaba MaaS 공식 설명](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/README.md)의 `instance_week_5min.zip`이다. [기존 bucket 0의 전량 원본 감사](../alibaba_maas_2026/SOURCE.md)에 고정된 ZIP 크기·ETag·21개 멤버별 CRC32/SHA-256을 다시 확인하면서 추출했다. 원본 140,906,827행 중 `SHA256(UTF-8 정규화 instance_id)` 첫 8바이트 정수 `%100 == 1`인 ID **46,938개·1,431,995행**을 보존했다. `%100 == 0`인 기존 bucket 0의 1,462,865행은 재확인하고 제외했다.

추출은 [새 ID 검증 규약](../../../reports/maas_existing_model_new_id_protocol.md), 저장 모델·코드·시험의 [접근 전 고정 manifest](../../../artifacts/maas_existing_model_new_id_v1/release_manifest.json)를 확인한 뒤에만 실행했다. 추출 감사 JSON (`instance_week_5min_bucket1_audit.json`)은 21개 조각의 원본 해시, HTTP Range 응답, 행수·스키마와 합본 해시를 보존한다. 합본 `instance_week_5min_bucket1.parquet` SHA-256은 `86b9dd55275ffd6819b0f482c8160e116fbe1980bb490ca8c09755107369f00d`다. 원본 ZIP과 각 임시 원본 조각은 저장하지 않았다.

원래 ID는 파일에 없다. 그러나 공개 익명 ID의 무염 SHA-256은 재연결될 수 있으므로 행별 표본과 예측을 대외 재배포하지 않는다. 현재 자료는 같은 공개 서비스의 같은 주간에서 ID만 분리한 표본이다. 별도 날짜·서비스·GPU 세대의 검증이 아니며 VM 증감 명령이나 사용자 SLO가 연결된 자료도 아니다.
