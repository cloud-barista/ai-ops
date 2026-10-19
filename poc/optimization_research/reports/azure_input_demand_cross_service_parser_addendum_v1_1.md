# Azure Conversation 입력 부하 외부 시험: 시각 파서 복구 규약 v1.1

상태: **v1.1 점수·지원량 집계 전 고정할 부록**. 원래 [교차 서비스 규약](azure_input_demand_cross_service_protocol.md), [v1 평가 코드](../scripts/evaluate_azure_input_demand_cross_service.py), [v1 릴리스 manifest](../artifacts/azure_input_demand_cross_service_v1/release_manifest.json)은 변경하지 않는다. v1의 날짜, 5분 구간, 적격 기준, Coding 모델과 네 비교기, 손실·피복률 및 통과 조건을 그대로 적용한다.

## 중단 기록과 확인된 노출

Conversation 원본을 공식 크기 **1,135,195,393바이트** 및 SHA-256 `a0cc9b969a9bbf0fd811802cbf4323edd3a209ace791e3799ad4f9207f213941`로 검증한 뒤, v1 `score_once`는 첫 CSV chunk의 시각 파싱에서 중단됐다. pandas가 소수초 형식을 추론한 상태에서 첫 chunk의 위치 **52,568**에 있는 정수초 UTC 문자열 `2024-05-12 00:35:42+00:00`를 거부했다. [파싱 실패 기록](../artifacts/azure_input_demand_cross_service_v1/parser_failure.json) SHA-256은 `83db06706496afdef4053b25a2f134a8a68422672b42dc437607125ad22acbbc`다. 그 위치와 문자열은 이미 노출됐다. 기존 자료 타당성 조사에서 보았던 양 끝 4KiB의 원시 입력 길이 노출도 유지한다.

중단 시 전체 5분 집계는 완성되지 않았고 지원량 게이트, 모델 로드, 예측, 점수 계산은 실행되지 않았다. v1의 `conversation_result.json`과 `conversation_scoring_started.json`도 생성되지 않았다. 따라서 이 복구는 관측된 모델 손실에 맞춘 조정이 아니다. 그래도 소스의 한 시각 값에 반응한 코드 변경이므로 완전히 사전 고정된 원래 v1 시험 결과로 포장하지 않는다.

## 유일한 코드 변경

v1.1의 CSV 집계 경로는 Coding의 `aggregate_source`와 동일한 두 열(`TIMESTAMP`, `ContextTokens`), `int64` 입력, 750,000행 chunk, UTC 변환, 정렬·양수·날짜 범위·행 수 검사를 쓴다. **시각 변환 호출 한 곳에만** `format="ISO8601"`을 명시한다: `pd.to_datetime(chunk["TIMESTAMP"], format="ISO8601", utc=True, errors="raise")`. 소수초가 있거나 없는 유효 ISO 8601 UTC 문자열을 같은 chunk에서 처리하기 위한 형식 지정이다. `GeneratedTokens`는 읽지 않는다.

그 뒤의 `design_from_aggregates`, 지원량 감사, 고정 모델·네 기준선의 예측, 날짜별 점수, 다섯 날짜 동일 가중 손실, 탐색 통과 게이트는 v1 코드와 Coding 모듈의 기존 함수를 호출한다. Conversation의 2024-05-12~13은 점수 없는 이력이고, 05-14~18 다섯 날짜만 한 번 평가한다. 각 시험일 부적격 구간 14개 이하, 첫·마지막 5분 구간 및 일곱 날짜 각각 행 존재를 **모델 로드 전에** 확인한다. 목표 구간이 0이더라도 결정 전 적격이면 포함한다.

## v1.1 단회 규칙

새 릴리스 manifest는 공식 Conversation 원본 SHA·크기, 이 부록/새 코드/합성 시험 SHA, 원래 Coding 및 v1 교차 서비스의 고정 파일 SHA, 위 파싱 실패 기록 SHA를 연결한다. 독립 검토에서 확정한 manifest SHA를 명령 인자로 제공해야 점수화한다. 실데이터의 전체 집계나 점수는 그 뒤에만 계산한다.

v1.1 평가가 지원량 부족, 다른 파싱 오류, 해시·스키마 오류 또는 성능 게이트 실패로 끝나면 해당 실패를 기록하고 **같은 Conversation 자료에 대해 파서나 평가 규칙을 다시 바꿔 재시도하지 않는다**. 새로운 자료와 별도 사전 규약이 없는 추가 시험은 독립 근거가 아니다. 통과하더라도 원래 규약의 주장 범위, 곧 같은 주 Azure 두 서비스의 공개 표본 입력 토큰 부하 예측에 한정한다.
