# Research Policy

실측 실행 기록의 후보별 보상을 학습해 실행 구성의 효용을 예측하는 경희대 연구 알고리즘입니다.

## 핵심 기능

- Random Forest 기반 후보별 보상 예측
- 자원 제약을 만족하는 후보 중 예측 효용이 높은 구성 선택
- 예측 신뢰도와 학습 범위를 검사한 판단 보류

`platform/research_policy.py`가 이 구현을 사용합니다. 오프라인 replay와 shadow 판단은 실제 VM 변경을 수행하지 않습니다.

## 설치

```bash
python -m pip install -e ./poc/research_policy
```

학습 원본 DB와 모델 파일은 별도 준비가 필요합니다. 모델 파일은 신뢰할 수 있는 출처에서만 불러옵니다.

[전체 구조](../../docs/architecture.md) / [학습과 PoC 연동](../../docs/getting-started.md#연구-정책)
