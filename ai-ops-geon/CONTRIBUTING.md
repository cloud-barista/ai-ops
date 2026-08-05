# 기여 가이드

AI service-control prototype 저장소에 관심을 가져 주셔서 감사합니다.

이 프로젝트는 AI LLM 운영 관리, 에이전트 등록 관리, AI 응용 배포·제어 전략 검증을 위한 1차년도 Go 기반 기능 프로토타입입니다.

## 기여 유형

다음 방식으로 기여할 수 있습니다.

- Issue 등록
  - bug report
  - feature request
  - enhancement suggestion
  - project question

- Pull Request 등록
  - documentation update
  - source code improvement
  - bug fix
  - test code addition
  - refactoring

위 예시에 한정되지 않으며, 프로젝트 개선에 도움이 되는 기여는 환영합니다.

## 기본 규칙

기여 전 다음을 지켜 주세요.

- 존중 있고 건설적으로 소통합니다.
- 하나의 기여는 하나의 명확한 목적에 집중합니다.
- 제출 전 코드가 정상 실행되는지 확인합니다.
- commit message와 Pull Request 설명을 명확히 작성합니다.
- 관련 log와 error message가 있으면 함께 공유합니다.

[행동강령](./CODE_OF_CONDUCT.md)을 읽어 주세요. 이 프로젝트에 참여하면 해당 조건을 따르는 것에 동의한 것으로 봅니다.

## 개발 가이드

이 프로젝트는 다음 개발 방향을 따릅니다.

- Go 언어 개발을 기준으로 합니다.
- 2종 이상 LLM 기반 coding agent role을 교차 검증에 활용합니다.
- framework prompt와 shared prompt document를 중심으로 개발 기록을 정리합니다.
- log와 error message는 명확하고 충분하게 기록합니다.
- coding agent를 구현에 활용할 수 있지만, 최종 review, test, verification은 사람이 수행합니다.

## 기여 절차

기여 전 기존 issue와 Pull Request를 확인해 주세요.

오타 수정이나 문서 개선처럼 작은 변경은 바로 Pull Request를 열 수 있습니다.

새 기능 추가, 구조 변경, 주요 workflow 수정처럼 큰 변경은 먼저 issue를 열어 팀과 논의해 주세요.

Pull Request 설명에는 change summary, validation command, expected reviewer note, relevant log를 포함합니다.

## Pull Request

Pull Request에는 다음을 포함해 주세요.

- 무엇을 변경했는지
- 왜 변경했는지
- 어떻게 테스트했는지
- reviewer가 알아야 할 점

reviewer가 수정을 요청하면 같은 branch를 수정해 다시 push합니다. Pull Request는 자동으로 갱신됩니다.

## 문서

프로젝트 산출물은 GitHub로 관리합니다. 주요 문서는 다음을 포함할 수 있습니다.

- 요구사항 정의서
- 기능/API 가이드
- 설치 및 실행 가이드
- 테스트 가이드

문서는 간결하고 실용적으로 작성합니다.

## 질문

기여 절차가 불확실하면 큰 변경을 시작하기 전에 issue를 열거나 maintainer에게 문의해 주세요.

프로젝트 개선에 도움이 되는 모든 기여를 환영합니다.
