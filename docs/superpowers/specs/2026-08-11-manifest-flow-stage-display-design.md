# Manifest Flow 단계 표시 설계

## 목표

Deployment Manifest 영역에서 Revision 1과 Revision 2를 서로 다른 실행처럼 보이지 않도록 하면서도, 각 생성 단계와 결과를 명확히 구분한다.

## 화면 구조

- 선택된 실행의 `Flow ID`를 Manifest 영역 상단에 표시한다.
- `Revision 1`을 독립 패널로 표시한다.
  - 단계: 배포 판단
  - 생성 조건: AIApplicationAutomationAgent의 DEPLOY 판단과 Go Guard 승인
  - 결과: 초기 Deployment Manifest
- `Revision 2`를 독립 패널로 표시한다.
  - 단계: 운영 최적화
  - 생성 조건: 배포 상태와 성능 Feedback 수신 후 OperationOptimizationAgent 및 Scaling Guard 승인
  - 결과: 최적화 Deployment Manifest
- 두 패널에는 동일한 `Flow ID`를 각각 표시한다.
- 실행 전에는 `Flow 미생성`, `Revision 생성 대기` 상태를 표시한다.

## 데이터 규칙

- Revision 1과 Revision 2는 별도 Flow가 아니다.
- 두 Revision은 동일한 `correlation_id`를 공유한다.
- 화면은 `manifest_revisions`에서 revision 번호별 결과를 분리해 렌더링한다.
- Revision 2가 아직 없으면 Revision 1만 결과로 표시하고 Revision 2는 Feedback 대기 상태로 둔다.

## 검증

- 실행 전 두 패널이 대기 상태인지 확인한다.
- Revision 1 생성 후 두 패널에 같은 Flow ID가 표시되고 Revision 1만 생성되는지 확인한다.
- Feedback 전송 후 Revision 2가 같은 Flow ID로 생성되는지 확인한다.
- 기존 실험 결과 화면의 Revision 렌더링을 깨뜨리지 않는지 테스트한다.
