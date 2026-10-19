# 운영 최적화 연구

AI 응용의 실측 기록과 공개 워크로드를 이용해 실행 성능, 자원 구성과 판단 효율을 평가한 연구 코드입니다.

## 연구 범위

- **실측 성능 예측:** 배치 지연, 처리량, GPU 메모리 피크와 모델 적재시간
- **자원 구성 평가:** CPU affinity와 같은 호스트의 GPU 복제본 후보 비교, 지원 범위 검사와 판단 보류
- **판단 추론 효율화:** 동일 후보의 예측 재사용, 후보 묶음 처리와 병렬 처리 비교
- **워크로드 분석:** 요청량, 토큰 수요와 GPU 사용 추적 자료를 이용한 예측·보정·용량 정책 실험

## 파일 구성

- [`scripts/`](scripts/): 학습, 평가, 예측과 결과 재현 코드
- [`policies/`](policies/): 플랫폼과 분리한 정책 계산 모듈
- [`contracts/`](contracts/), [`examples/`](examples/): 입출력 스키마와 요청 예시
- [`tests/`](tests/): 계약, 데이터 분할, 경계 조건과 알고리즘 시험
- [`reports/`](reports/): 실험 규약, 결과와 채택 여부
- [`sources/`](sources/): 외부 자료의 출처와 라이선스

[연구별 기록](RESEARCH_NOTES.md)에서 관련 코드와 보고서를 찾을 수 있습니다. 보고서는 성공 결과뿐 아니라 기준선보다 나빠졌거나 사전 평가 기준을 통과하지 못한 실험도 포함합니다.

## 환경과 실행

Python 3.10~3.12와 연구 당시의 고정 패키지 버전을 사용합니다.

아래 재현 명령은 저장소 루트의 Linux 셸 기준입니다. `scripts` 간 모듈을 불러올 수 있도록 연구 디렉터리를 Python 경로에 추가합니다.

```bash
python -m venv .venv
python -m pip install -r poc/optimization_research/requirements.txt
python -m pip install pytest
export PYTHONPATH="$PWD/poc/optimization_research${PYTHONPATH:+:$PYTHONPATH}"
python poc/optimization_research/scripts/train_performance.py --help
```

원자료와 학습 산출물은 포함하지 않습니다. 공개 자료는 `sources`의 출처를 확인해 준비하고, 연구별 보고서의 명령과 입력 경로를 사용합니다. `data`와 `artifacts`는 자료를 준비하거나 스크립트를 실행한 뒤 생성되는 경로입니다. 고정 모델의 해시·패키지 버전을 검사하는 실험에는 당시 원본 산출물이 필요합니다.

원자료 없이 실행할 수 있는 계약 시험의 예:

```bash
python -m pytest poc/optimization_research/tests/test_data_contract.py
```

전체 시험 중 일부는 원본 DB, 공개 데이터와 고정 모델을 요구합니다. GitHub CI에서 실행하는 계약 시험과 원자료를 준비한 뒤 실행하는 재현 실험을 구분합니다.

이 디렉터리의 오프라인 예측·평가 결과가 실제 VM 배포 또는 자동 운영 성능을 입증하지는 않습니다. 통합 플랫폼과의 연결 범위는 [연동 구조](../../docs/architecture.md)를 참고하세요.
