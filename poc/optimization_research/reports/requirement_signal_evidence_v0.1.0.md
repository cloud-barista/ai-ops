# 응용 요구 신호의 요청 결속 연구 증거 v0.1.0

기존 [요구 신호 평가기](../scripts/assess_requirement_signals.py)는 SQLite에서 학습한 고정 모델로 관측 CUDA 예약 피크와 로컬 캐시 모델 적재시간을 각각 추정한다. 이번 [생성기](../scripts/generate_requirement_signal_evidence.py)는 평가기에 제출한 **전체 요청과 두 신호의 결과**를 [버전형 JSON](../contracts/requirement_signal_evidence_v0.1.0.schema.json)으로 함께 저장한다. [검증기](../scripts/validate_requirement_signal_evidence.py)는 원 요청 해시·증거 ID·스키마·출처와 단위를 검사한 뒤, 고정 원본과 모델을 다시 읽어 결과를 재계산한다. Go PoC와 배포 기능은 변경하지 않았다.

증거의 `request_sha256`은 키 정렬·공백 없는 UTF-8 JSON의 SHA-256이다. `evidence_id`는 `generated_at`과 ID 자체를 제외한 증거 전체의 같은 방식 해시이므로 같은 요청·결과를 다른 시각에 재생성해도 같다. 이 해시는 **서명이나 등록 메타데이터 진위 증명은 아니다.** 원 요청을 검증기에 함께 주면 증거에 저장된 요청과 정확히 일치하는지도 확인한다. 요청에는 모델 ID·고정 revision, 등록 시점이라고 선언한 파라미터 수/바이트·출처/시각, 입력 조건과 물리 프로필이 포함된다. `input_metadata_verified=false`가 이 선언의 한계를 명시한다.

생성기는 형식이 잘못된 요청을 모델 로드 전에 거절한다. 형식은 맞지만 학습 지원 밖인 요청은 기존 평가기의 `ABSTAIN`을 보존한다. 한 신호만 보류되면 다른 신호의 추정치를 계속 기록하고, 보류값을 0으로 채우지 않는다. `RESEARCH_ESTIMATE`에는 양수 유한 추정치와 빈 이유 목록, `ABSTAIN`에는 `null` 추정치와 이유 목록이 필요하다. 스키마는 VM·노드·배포 행동 필드를 허용하지 않는다. 모델·원본 해시가 다르면 예측과 파일 쓰기 전에 종료하며, 출력 파일은 임시 파일을 완성한 뒤 원자적으로 생성하고 기존 파일을 덮어쓰지 않는다.

## 재현

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/generate_requirement_signal_evidence.py \
  --request examples/requirement_signals_request.json \
  --generated-at 2026-09-29T05:00:00Z \
  --output /tmp/requirement_signals_evidence.json
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  scripts/validate_requirement_signal_evidence.py \
  --input /tmp/requirement_signals_evidence.json \
  --request examples/requirement_signals_request.json
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/python \
  -m unittest discover -s tests -p test_requirement_signal_evidence.py -v
```

[저장 예시](../examples/requirement_signals_evidence_example.json)는 기존 합성 등록 요청에서 나온 CUDA 예약 피크 `0.20821557769515572 GiB`, 로컬 캐시 적재시간 `0.1875111137242485초`를 그대로 담는다. 계약 테스트 9개는 두 수치와 재계산 일치, 파라미터 메타데이터만 바꾼 요청의 새 해시·ID, 한 신호만 보류되는 경우, 학습 revision을 새 모델 ID로 재명명한 요청의 보류·재검증(대문자 16진수 표기 포함), 잘못된 시간 순서의 사전 거절, 값·단위·행동 필드 변조 거절, 모델 해시 오류의 역직렬화 전 중단, 파일 무덮어쓰기를 확인한다.

이 신호의 대상은 **한 호스트·한 GPU 인덱스의 관측 PyTorch 예약 피크와 캐시된 모델 적재시간**이다. 최소 VRAM, 실제 VM 메모리·노드 수, 준비시간, OOM·사용자 SLO, `DEPLOY`/`SCALE_OUT` 효과를 측정한 결과가 아니다. 검증기의 재계산은 현재 고정 모델과 같은 입력에서 계산 결과가 일치함을 확인할 뿐, 호출자가 선언한 `available_at`이 실제 등록 로그에 존재했는지는 확인하지 못한다. 새 코드/모델 버전에서는 이전 증거를 해석할 코드와 아티팩트를 별도로 보존해야 한다.
