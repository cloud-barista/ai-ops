# RAGPulse 입력 수요 이전 시험: 지원량 부족으로 무채점

**판정: `SUPPORT_NO_GO_NO_SCORE`.** Azure Coding에서 학습한 상대 수요 90분위 모델의 전날 보정 이전 시험을 [사전 규약](ragpulse_input_demand_transfer_protocol.md)대로 RAGPulse 공개 표본의 상대일 28–34에 계획했다. 모델·네 기준선에 점수를 매기기 전에, 직전 완료 1시간의 평균 표본 입력 토큰이 1,000 이상인 5분 구간이 각 날짜의 **최소 274/288**개인지 확인했다. 전날 보정을 위한 27일과 시험 7일 모두 이 기준에 못 미쳤다.

| 상대일 | 적격 5분 구간 / 288 | 사전 하한 274 통과 |
| ---: | ---: | :---: |
| 27, 보정 이력 | 234 | 실패 |
| 28 | 231 | 실패 |
| 29 | 225 | 실패 |
| 30 | 245 | 실패 |
| 31 | 250 | 실패 |
| 32 | 241 | 실패 |
| 33 | 250 | 실패 |
| 34 | 244 | 실패 |

[원본](../data/external/ragpulse_2025/SOURCE.md)은 34,987행이고 바이트 해시가 공식 Git blob과 일치한다. 본문을 받기 전에 Coding 원본으로 주말·절대 시각 특징이 없는 모델을 학습해 [v1 freeze](../artifacts/ragpulse_input_demand_transfer_v1/freeze.json)에 모델·코드·시험·규약 해시를 기록했다. 첫 평가 실행은 JSON `timestamp`의 숫자 문자열 형식 때문에 **첫 행에서**, 원본 집계·지원량·점수 전에 중단됐다. [v1.1 파서 부록](ragpulse_input_demand_transfer_parser_addendum_v1_1.md)의 변경과 [점수 전 release manifest](../artifacts/ragpulse_input_demand_transfer_v1_1/release_manifest.json)를 별도로 고정한 뒤 지원량만 다시 계산했다. [최종 무채점 JSON](../artifacts/ragpulse_input_demand_transfer_v1_1/support_no_go.json)에 8일의 적격 수를 남겼다. `evaluation_started.json` 및 모델 시험 `result.json`은 생성되지 않았다.

독립적인 두 번째 집계에서도 원본 34,987행을 `timestamp // 300`으로 합치고 각 판단 시점의 앞선 12구간 평균을 계산해 **8일의 적격 수가 모두 정확히 일치**했다. 합성 시험 8개는 같은 시각의 여러 행 유지, 문자열·숫자 시각의 구간 배정, 미래 값 누출 방지, 전날만 이용한 보정, 모든 단순 기준선에 같은 보정을 적용하는지 확인했다.

이 결과는 **예측 성능 실패도 성공도 아니다.** 표집이 성긴 자료에서 사전 정의한 지원 범위가 충분하지 않아 점수 계산 자체를 중단한 것이다. 날짜나 문턱을 이 결과에 맞춰 바꾸어 같은 원본을 독립 시험으로 재사용하지 않는다. 공개 표본 합계는 실제 서비스 전체 수요, GPU·VM 수, 사용자 SLO, 배포·Scale-out/in 결과가 아니다.
