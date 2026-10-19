# BurstGPT v2.0 원본과 사용 범위

- 저자 [공식 저장소](https://github.com/HPMLL/BurstGPT/tree/7eb2c4f8350f8a6985272386f5c14af1f678b299), [v2.0 release](https://github.com/HPMLL/BurstGPT/releases/tag/v2.0), 고정 태그 commit `7eb2c4f8350f8a6985272386f5c14af1f678b299`.
- 사용 파일: release asset [`BurstGPT_3.csv`](https://github.com/HPMLL/BurstGPT/releases/download/v2.0/BurstGPT_3.csv), 231,682,327 bytes, SHA256 `2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f`. GitHub release API의 asset digest와 실제 내려받은 원본 해시가 일치한다. 2026-09-28 수집.
- 라이선스: [저장소의 CC BY 4.0](https://github.com/HPMLL/BurstGPT/blob/7eb2c4f8350f8a6985272386f5c14af1f678b299/LICENSE). 저자 요청에 따라 [BurstGPT 논문](https://doi.org/10.1145/3711896.3737413)을 인용한다.
- 재현: `python scripts/fetch_burstgpt_v2.py`로 원본을 받고 `python scripts/audit_burstgpt_v2.py`로 전체 행의 스키마·시각·결측을 확인한다. 생성되는 `audit.json` (`audit.json`)은 내려받은 원본의 검사 결과다.

`BurstGPT_1.csv`와 `_2.csv`에는 지연시간 라벨이 없는 것을 공식 release 파일의 HTTP Range 헤더에서 확인했다. `_3.csv`에만 `Elapsed time` 열이 있다. 저자는 이를 요청 제출부터 전체 응답 완료까지의 시간(초)으로 정의한다. `Timestamp`는 첫날 자정 기준 상대 초이며 공개 파일에 절대 날짜 대응표는 없다. `Elapsed time`은 GenTD26의 이미지 생성 `exec_time_seconds`와 같은 타깃이 아니므로 두 자료의 예측기를 직접 합쳐 학습하지 않는다.

**입력 누출 방지:** 요청 시점 예측 입력은 `Timestamp`에서 계산한 과거 시각, `Model`, `Request tokens`, `Log Type`로 제한한다. `Response tokens`와 `Total tokens`는 응답 완료 후에 확정되므로 예측 입력으로 사용하지 않는다. 잔차 피드백은 적어도 `Timestamp + Elapsed time` 이후에만 가용하며, 별도 로그 수집 지연이 있으면 추가해야 한다. 전체 감사에서 0초 경과시간이 59.4%이고 날짜별 비율이 크게 달라 0초를 정확한 완료시간으로 단정할 수 없다. 이 자료를 요청 지연 검증에 쓸 때는 품질 감사 보고서 (`../../../reports/burstgpt_data_audit.md`)의 한계를 함께 제시한다.
