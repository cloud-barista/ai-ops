# Research Policy

실측 실행 기록의 후보별 보상을 학습해 실행 구성의 효용을 예측하는 경희대 연구 알고리즘입니다.

## 핵심 기능

- Random Forest 기반 후보별 보상 예측
- 자원 제약을 만족하는 후보 중 예측 효용이 높은 구성 선택
- 예측 신뢰도와 학습 범위를 검사한 판단 보류
- 로컬 Hugging Face 추론 실측 수집과 SQLite 저장
- 실측 기록의 후보 평가 데이터 변환, 학습과 평가 CLI

`platform/research_policy.py`가 이 구현을 사용합니다. 오프라인 replay와 shadow 판단은 실제 VM 변경을 수행하지 않습니다.

## 설치

```bash
python -m pip install -e ./poc/research_policy
```

학습 원본 DB와 모델 파일은 별도 준비가 필요합니다. 모델 파일은 신뢰할 수 있는 출처에서만 불러옵니다.

```bash
python -m khu_policy_agent --help
python -m khu_policy_agent train --dataset-db <dataset.sqlite3> --artifact-dir <model-directory>
python -m khu_policy_agent evaluate --dataset-db <dataset.sqlite3> --artifact-dir <model-directory>
```

실측 수집기는 Linux의 `resource`와 CPU affinity 기능을 사용합니다. `collect-live`에는 PyTorch, Transformers와 로컬 모델 캐시를 별도로 준비해야 합니다. 명령을 실행할 때 실제 모델을 적재하고 추론하며, VM을 생성하지 않습니다.

정책 및 수집기 시험은 Linux에서 `python -m pytest poc/research_policy/tests`로 실행합니다. [설계/API 명세](docs/설계_및_API_명세.md)는 초기 shadow 정책의 책임 경계를 기록한 자료입니다.

[`scripts/`](scripts/)에는 실측 수집과 CB-Tumblebug 캠페인용 실행 도구를 포함했습니다. [수집 환경 구성](docs/TUMBLEBUG_COLLECTION.md)을 먼저 확인하고 운영 환경에 맞는 연결 정보를 별도로 설정합니다. 이 도구들은 통합 플랫폼을 시작할 때 자동으로 실행되지 않습니다.

[전체 구조](../../docs/architecture.md) / [학습과 PoC 연동](../../docs/getting-started.md#연구-정책)
