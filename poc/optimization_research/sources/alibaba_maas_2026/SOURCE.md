# Alibaba MaaS 2026 공개 자료의 출처와 경계

- 공식 설명: [MaaS trace README](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/README.md), [스키마](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/docs/schema.md), [다운로드 목록](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/docs/data_download.md).
- 공식 저장소 고정 commit: `8a6295f9779ddc019add669eeca49c5f9f77b3a7`. 로컬 다운로드: 2026-09-28.
- 사용한 응용 사례 자료: `app_metrics.csv.zip`(99,947바이트), 그 안의 `app_metrics.csv`(1,757,150바이트), 같은 commit의 `app_cases.json`. `python scripts/fetch_maas_app_cases.py`로 다시 받을 수 있다.
- SHA256: ZIP `c03d3a0cd81d1bd1060eee5035022ea071879817b794cc0c7bf2ad6659cfe927`; CSV `ac536b99cd23d4ada0ff85925627a3d71cce88d242fdb4c645612bb657f20496`; 사례 설명 JSON `67a38e16e1d9af90d84a4f2b20c6e05e19c72f69fb9a5bfe763ec9e5b4adc304`.

이 사례 파일은 7일간의 시간별 관측 7,401행이다. 응용 4개 가운데 3개에 정규화 QPS가 있고, 2개에만 QPS와 배정 GPU 수가 모두 있다. 공식 `app_cases.json`에 따르면 `qps_norm`은 **공개 주간 전체의 응용별 최고 QPS**로 나눈 값이며 그 분모는 공개되지 않았다. 정규화 자체가 사후 주간 최고값에 의존하므로 온라인 결정 시 알 수 있는 입력이라고 가정할 수 없다. 따라서 응용 간 절대 요청 수를 비교하거나 VM당 실제 처리량을 역산하지 않는다. `gpu_count`는 배정된 GPU 상당량이고, 사용자 요청에 대한 실제 스케일 실행 로그가 아니다. TTFT·TPOT 값은 관측된 시간별 평균이며 사용자 SLO 목표나 분포 꼬리값이 아니다.

전체 일별 자료는 10,528,173,427바이트 ZIP(`instance_daily`, 61개 Parquet 조각, 약 6개월), 5분 단위 자료는 4,812,189,789바이트 ZIP(`instance_week_5min`, 21개 조각, 1주)이다. 전체 자료를 내려받지 않고 ZIP의 저장된 `instance_daily/part_0.parquet` 멤버만 HTTP byte range `59-171991185`로 확인했다. 이 조각의 SHA256은 `8c56f76d2887cdda5594be4f91686c75b4d1b3756dbaff9a7859c2f2e7a40468`; 2,356,728행에 **서로 떨어진 14일**만 들어 있다. 이 단일 조각의 일별 합계를 6개월 연속 부하로 취급하면 관측 누락을 실제 수요 변화로 잘못 해석한다. 따라서 구조 진단 뒤 로컬 조각을 삭제했고 학습·평가에는 넣지 않았다.

## 5분 인스턴스 자료의 전량 감사와 연구 표본

`scripts/fetch_maas_instance_sample.py`로 주간 ZIP의 **21개 조각을 모두** HTTP Range로 순차 검증했다. 수집 당시 코드 SHA256은 `dbe71d5228f38d7d79144eb4f7ea47deb4342f3244542fdaa95fee51eaadf2ba`다. 이후 저장 조각 재사용 시 원본 메타데이터 대조를 강화해 코드 해시가 달라졌다. 원격 ZIP은 4,812,189,789바이트, ETag `434E78D81E2A9F9F1FDEF3163DD0FCEC`이다. 각 조각의 Range 응답·CRC32·SHA256·스키마를 검증했고, 공식 총행수 140,906,827행과 일자 140–146, 5분 정렬을 확인했다. 조각별 해시와 열 감사는 `instance_week_5min_audit.json`에 있다. 전체 ZIP과 전체 추출본은 로컬에 저장하지 않았다.

원본 `instance_id`의 UTF-8 문자열에 SHA256을 적용하고 첫 8바이트 정수를 100으로 나눈 나머지가 0인 ID를 전 조각에서 일관되게 선택했다. 전체 고유 ID 4,681,106개 중 46,642개, 전체 행 140,906,827개 중 1,462,865개가 선택됐다. 8개 열만 남긴 `instance_week_5min_sample.parquet`의 SHA256은 `6f52288916f7a8be8bacb2d911bc20294ab7f6685eda2b1f0f63cfc77af77e82`다. 원래 ID는 저장하지 않았지만 공개 익명 ID의 무염 해시는 다시 연결할 수 있으므로 행 단위 표본과 예측 파일을 대외 배포하지 않는다.

원자료 전체에서 `sm_util` 결측은 36,959,373행(26.2%), `gpu_mem_util` 결측은 9,155,346행(6.5%)이다. GPU 메모리 사용률이 100%를 넘는 값은 53,533행(0.038%, 최대 275.23%)이었다. [공식 스키마](https://github.com/alibaba/clusterdata/blob/8a6295f9779ddc019add669eeca49c5f9f77b3a7/cluster-trace-v2026-maas/docs/schema.md)는 이를 백분율로 설명하지만 초과 원인은 공개되지 않았다. 사전 실험 규약 (`../../../reports/maas_instance_risk_protocol.md`)에 따라 결측과 `[0,100]` 범위 밖 계측은 학습·정답에서 제외한다. 선택 표본에는 같은 ID·시각이 서로 다른 클러스터 또는 GPU 할당으로 기록된 키 50,839개와 5분 간격이 빠진 연속 쌍 21,328개가 있다. 같은 시각의 복수 행은 유일한 한 상태로 정할 수 없어 위험 예측의 과거·미래 창에서 제외한다. 적격 비율은 별도 평가 감사표로 공개한다.

로컬 SQLite, GenTD26, MaaS는 모델·시스템·시각축이 서로 다르다. 원시 행을 결합해 하나의 수요·GPU 정답으로 학습하지 않는다. 이 자료 어디에도 VM 증설 명령과 전후 반사실 결과가 없으므로 `SCALE_OUT` 또는 `SCALE_IN`의 효과를 검증할 수 없다.
