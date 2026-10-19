# Alibaba MaaS bucket1 추출기 준비 상태

2026-09-29. [SM-only 관측 위험 규약](maas_sm_observability_protocol_v1.md)의 새 인스턴스 ID 시험용 **bucket1 추출 코드만 준비했다. 실제 bucket1 원본은 아직 받거나 열지 않았고 정답·모델 점수도 계산하지 않았다.** 학습·검증 게이트가 통과하고 아래 해시 고정 파일을 검토하기 전에는 실행하지 않는다. 현재 명령의 기본 동작은 네트워크에 접속하지 않고 안내문만 출력한다.

[추출기](../scripts/fetch_maas_instance_bucket1.py)는 기존 [전수 감사 수집기](../scripts/fetch_maas_instance_sample.py)의 고정 ZIP HTTP Range, 중앙 디렉터리/로컬 헤더, 멤버 CRC32·SHA-256, 재개 가능한 임시 파일 검증을 재사용한다. 21개 `part_0`–`part_20`을 모두 요구하고, 각 멤버의 이름·바이트 오프셋·길이·CRC32·SHA-256·행 수를 기존 21개 전수 감사 (`../data/external/alibaba_maas_2026/instance_week_5min_audit.json`)와 대조한다. 이 감사의 고정 SHA-256은 `3a24d64122dcf8e1fd9f68627acbd70dd878924483b29c334ae74a612e75cb88`이다. 하나라도 달라지면 선택 파일을 성공으로 기록하지 않는다.

모든 원본 행에 동일한 `SHA256(UTF-8 정규화 instance_id)` 첫 8바이트 빅엔디언 정수의 `mod 100 == 1`을 적용한다. 기존 bucket0은 `==0`이므로 ID 집합이 겹치지 않는다. 선택한 행에서는 결측·범위 밖 계측과 같은 ID·시각의 복수 행도 보존하고 원래 ID 대신 전체 SHA-256과 원계측 열만 **로컬** Parquet에 쓴다. 조각별 감사와 출력 해시를 남기며 원본 ZIP을 저장하지 않는다. 원본 멤버는 최대 300 MB로 제한하고, 현재 멤버와 선택 임시 파일의 합계가 0.5 GB에 닿으면 중단한다. 한 멤버를 검증·선택한 뒤 원본 임시 파일을 삭제하고 다음 멤버를 읽는다.

실제 다운로드에 앞서 `artifacts/maas_sm_observability_v1/bucket1_release_manifest.json`을 별도로 작성·검토해야 한다. 추출기는 다음 필드를 모두 검증하고 **그 뒤에만** 네트워크를 연다.

| 필드 | 요구 사항 |
| --- | --- |
| `status` | `FROZEN_BEFORE_BUCKET1_ACCESS` |
| `source_url`, `source_size`, `source_etag`, `source_members`, `source_rows` | 코드에 고정한 공식 21조각 ZIP 값과 동일 |
| `id_bucket`, `bucket_modulus`, `validation_gate_pass` | 각각 `1`, `100`, `true` |
| `artifacts.protocol` | `reports/maas_sm_observability_protocol_v1.md`와 SHA-256 `79e82f9a03093b8423097cd6e8f628a58513effaddb12f5d587a5bc11b56774b` |
| `artifacts.source_audit` | 기존 21조각 감사 파일과 위 SHA-256 |
| `artifacts.training_script`, `artifacts.evaluation_script`, `artifacts.extractor` | 학습·평가 코드와 이 추출기의 상대 경로·실제 SHA-256; 학습과 평가가 같은 스크립트라면 두 항목이 같은 파일을 가리켜도 됨 |
| `artifacts.development_tests` | `tests/test_evaluate_maas_sm_observability_v1.py`와 실제 SHA-256 |
| `artifacts.fitted_models` | 저장 모델 파일 **전체**의 `{path,sha256}` 목록, 최소 1개 |
| `artifacts.calibration_parameters`, `artifacts.baseline_parameters` | 보정 온도·기준선 파라미터 파일의 상대 경로·실제 SHA-256; 한 파일에 함께 저장한 경우 같은 파일을 가리켜도 됨 |
| `artifacts.training_result`, `artifacts.validation_result` | 학습·검증 결과 JSON의 상대 경로·실제 SHA-256; 검증 결과에도 `validation_gate_pass: true` 필요 |

단일 파일을 가리키는 각 `artifacts.*` 값은 `{ "path": "research 루트 기준 상대경로", "sha256": "64자리 해시" }` 형태다. `fitted_models`는 같은 형태의 배열이다. 경로는 research 루트 내부 파일이어야 한다. 검증 결과 JSON은 `protocol_sha256`, `training_script_sha256`, `evaluation_script_sha256`, `development_tests_sha256`, `training_result_sha256`, `calibration_parameters_sha256`, `baseline_parameters_sha256`, `fitted_models_sha256`(모델 상대경로→SHA-256 매핑), `dependency_versions`(Python·NumPy·pandas·scikit-learn·PyArrow), `validation_gate_pass: true`를 기록해야 한다. 추출기는 이 값을 release manifest의 실제 파일 해시와 다시 맞춘다. 코드·규약·테스트·모델·파라미터·결과·기존 소스 감사가 매니페스트 작성 뒤 바뀌면 접근을 거절한다. 검토가 끝난 **뒤에만** 아래 명령을 실행한다. 현재는 별도로 검토·고정한 release manifest가 없어 이 명령의 실행 조건을 갖추지 못했다.

```bash
cd /home/researcher/ai-mcmp/research
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/fetch_maas_instance_bucket1.py --fetch --release-manifest artifacts/maas_sm_observability_v1/bucket1_release_manifest.json
```

실데이터를 읽지 않는 [합성 시험](../tests/test_fetch_maas_instance_bucket1.py)은 21개 가짜 ZIP 조각의 반복 ID를 전부 선택하는지, bucket0·다른 버킷을 제외하는지, 무효 계측·복수 행을 보존하는지, 한 멤버의 이전 전수 감사 SHA가 달라지면 중단하는지, 원격 클라이언트 주입과 동결 뒤 코드·테스트·검증 결과 연결 변경을 거부하는지 검사한다. **6개 시험이 통과했다.** 재현 명령은 다음과 같다.

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p 'test_fetch_maas_instance_bucket1.py' -v
```

추출 성공 후에도 이 자료는 조치·Ready·사용자 SLO의 연결을 제공하지 않는다. 학습·검증·시험의 주장 범위는 고정된 [SM-only 규약](maas_sm_observability_protocol_v1.md)을 따른다.
