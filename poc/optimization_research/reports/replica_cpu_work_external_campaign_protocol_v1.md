# GPU 복제 CPU 작업량: 별도 로컬 수집 캠페인 고정 시험

2026-09-29. [첫 CPU 작업량 학습](replica_cpu_work_findings_v1.md)의 HGB는 8 encoder 중 한 모델에서 Ridge보다 `2.13배` 나빠 채택하지 않았다. 이 시험은 **같은 모델·호스트의 별도 물리 수집 캠페인**에서 그 실패가 반복되는지 확인한다. 첫 시험의 모델별 점수는 이미 공개됐으므로 이 후속 평가를 새로운 모델에 대한 독립 검증이라고 부르지 않으며, 결과가 좋아도 앞선 미채택 결정을 소급해 바꾸지 않는다.

## 고정 자료와 적격성

- 원본은 SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`의 `data/audit_v1/measurements.parquet`다. 학습은 오직 `khu-hf-local-exhaustive`의 8 encoder 모델·2,048짝에서 한다. 별도 시험은 `khu-hf-live`와 `khu-hf-diverse-cpu-matrix`다. 둘은 메인보다 **앞서** 수집됐으므로 시간순 미래 검증이 아니다.
- 짝 키는 기존 키 `(campaign,host,model_id,model_revision,workload_group,cpu_core_limit)`에 `run_id`를 더한다. 동일 키에 GPU1·GPU2가 한 행씩 있을 때만 시험한다. GPU1만 있는 `live` 440개 그룹과 `diverse` 32개 그룹은 정답 미관측으로 빼고 개수를 보고한다. GPU2만 있거나 중복되는 키는 오류다. GPU1 측정 종료가 GPU2 시작보다 먼저이고, 실행 프로필·입력 인코딩·GPU 인덱스·두 자식의 전체 예제 수·CPU초 합계가 기존 [검증 코드](../scripts/evaluate_replica_cpu_work.py)에서 모두 일치해야 한다. 이 검증에 실패한 짝은 사유별로 개수만 보고하고 **점수에서 제외**한다. 점수 전 감사에서 `live`의 Qwen 한 짝은 `input_encoding`이 `unspecified` 대 `tokenizer_text`라 탈락했다. 다른 오류가 있으면 학습·점수를 중단한다.
- 사전 기대 수: `live` 원시 완전 짝 660개 중 유효 659개(encoder 5모델×110회 = 550, Qwen 109), `diverse` 완전 짝 48개(encoder 5모델×8회 = 40, Qwen 8). Qwen은 주 학습의 동종 donor가 없어 학습기 `ABSTAIN`이다. 다섯 encoder의 모델 ID는 메인에 이미 있으며, 별도 캠페인의 `workload_group`은 메인과 겹치지 않는다. `live`는 모델당 한 프로필을 110회 반복했고 수집기 v1/v2가 섞여 있다. 550회를 550개의 독립 앱으로 세지 않는다.

## 고정 예측과 판정

기존 [v1 규약](replica_cpu_work_protocol_v1.md)의 GPU1 입력 9개·타깃 `GPU2` 두 자식 프로세스 CPU초 합계·네 방법의 설정을 **변경 없이** 재사용한다. 대상 모델별로 메인 캠페인의 다른 7 encoder 모델만으로 donor 비율, Ridge, HGB를 맞추고, `live`/`diverse`의 해당 모델 GPU1 결과에 적용한다. 별도 캠페인 GPU2 정답, 해당 모델 메인 정답, Qwen 정답으로 모델을 선택·보정하지 않는다. 입력은 대상 GPU1 실행 후에만 알 수 있다.

각 캠페인에서 모델별 평균 절대 로그오차와 5모델 균등 평균, 90분위 절대 로그오차, CPU초 MAE, 과소예측 비율을 보고한다. `live`는 수집기 v1/v2를 나누어 같은 점수를 추가로 보고한다. 반복 측정은 모델별로 묶고, `diverse`의 같은 작업 4개 CPU 설정도 모델 안에서 집계한다. HGB의 사전 안정성 조건은 두 캠페인 각각에서 **Ridge의 95% 이하 평균 절대 로그오차**이면서 **어느 모델에서도 Ridge의 120%를 넘지 않는 것**이다. `live` 수집기 v1/v2 각각에서도 모델 균등 평균 오차가 Ridge보다 높지 않아야 한다. 하나라도 실패하면 다른 캠페인에서의 평균 개선으로 덮지 않는다. 조건을 모두 통과해도 신규 모델·호스트·실제 VM 조치 검증이 아니므로 배포 판단에는 사용하지 않는다.

점수 전에 원본·기존 v1 freeze·기존 v1 코드·이 규약·새 평가 코드의 SHA-256과 적격성 감사 건수를 별도 `freeze.json`에 고정한다. 점수 뒤 가중치·문턱·제외 규칙을 바꾸지 않는다.
