# RAGPulse `timestamp` 숫자 문자열 파서 복구 v1.1

[v1 동결 기록](../artifacts/ragpulse_input_demand_transfer_v1/freeze.json) 뒤 공식 원본을 내려받았고, 크기 9,961,760바이트 및 Git blob SHA-1 `a058ecc31c56648b20709a8c211f21dd97c7d08b`를 확인했다. [실패 기록](../artifacts/ragpulse_input_demand_transfer_v1/parser_failure.json)처럼 평가기는 첫 JSON 행의 `timestamp`가 문자열 `"0"`인 것을 거부했다. 이 실패는 원본 전체 집계, 일별 지원량 감사, 모델 로드, 예측 및 점수 **전에** 발생했다. 복구를 위해 첫 JSON 객체에서 키 목록과 `timestamp`의 유형·표기, `input_length`의 유형만 확인했다. 입력 토큰 값이나 시험 기간의 요약·결과는 보지 않았다.

v1.1은 `timestamp`를 **유한한 비음수 십진 숫자 문자열**로도 받아 정확한 Decimal 초로 변환한다. 기존 숫자형도 허용한다. 초→5분 구간 배정, 시각 정렬·범위, `input_length` 정수·비음수, 원본 해시·행수, 모델, 기준선, 보정, 지원량과 성공 게이트는 [원래 시험 규약](ragpulse_input_demand_transfer_protocol.md) 그대로다. 원본을 다시 정렬하거나 기록을 삭제하지 않는다.

이 복구는 첫 행의 형식을 본 뒤 결정했으므로 시험 원본을 전혀 보지 않았다고 주장하지 않는다. v1과 별도 v1.1 평가 코드·시험·이 부록 및 기존 동결 파일을 묶어, 점수 전에 새 release manifest를 작성한다. v1.1에서도 지원량 실패 시 시험 점수를 계산하지 않는다.
