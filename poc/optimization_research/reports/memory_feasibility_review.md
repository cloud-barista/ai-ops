# SQLite 메모리 적합성 학습 가능성 검토

## 결론

현재 SQLite에는 **최소 VM RAM, 최소 GPU VRAM, GPU 종류별 실행 가능성**의 학습 정답이 없다. 성공한 GPU 실행 7,985건의 GPU당 예약 메모리 피크는 측정 장치 용량보다 훨씬 낮았고, 실패 10건은 모두 모델 파일을 가져오지 못한 오류였다. 따라서 이 자료로 메모리 사용량의 *관측 피크*를 회귀할 수는 있어도, 더 작은 VM이나 다른 GPU에서 실행 가능한지 또는 어떤 GPU 클래스를 추천해야 하는지 검증할 수 없다. 메모리 **적합성** 학습 모델은 만들지 않는다. 관측 피크 회귀는 별도 [탐색 실험](vram_peak_findings.md)으로 평가했다.

## 자료와 계측 정의

- 고정 스냅샷: `data/audit_v1/measurements.parquet` (`../data/audit_v1/measurements.parquet`), SHA-256 `b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906`. 원본 7개 캠페인의 `executions.sqlite3`와 `node_samples`는 읽기 전용으로 확인했다.
- 총 9,591행 중 성공 9,581행, GPU 성공 7,985행, CPU 성공 1,596행이다. 실패 10행은 모두 원격 캠페인의 `OSError`이며, `error_json.message`는 Hugging Face에 연결할 수 없고 로컬 캐시에 `config.json`이 없다고 기록한다. OOM 또는 메모리 제한으로 실패한 행은 0개다.
- `gpu_memory_allocated_bytes`와 `gpu_memory_reserved_bytes`는 PyTorch의 `max_memory_allocated`와 `max_memory_reserved`다. 다중 replica의 상위 DB 열은 **replica별 값을 합산**한다. 장치당 용량 비교에는 `result_json.replica_results` 안의 개별 예약 피크를 사용했다. `max_rss_kib`도 replica별 프로세스 RSS 고수위의 합이며, 최소 VM 메모리의 정답이 아니다. 근거: `huggingface_workload.py` (`/path/to/aiops/federated_coordination_agent/huggingface_workload.py`)와 원본 `live_collector.py` (`/path/to/aiops/khu_policy_agent/live_collector.py`) 내 계측·집계 코드.

## 용량 경계가 관측되는가

| 호스트·장치 | 성공 GPU 실행 | 성공 모델 | 장치당 물리 VRAM | 장치당 최대 예약 피크 | 피크/용량 | 호스트 RAM | 실행 중 최소 `MemAvailable` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `icns` · RTX PRO 5000 Blackwell × 2 | 7,154 | 9 | 47.788 GiB | 3.779 GiB | 7.9% | 109.791 GiB | 86.754 GiB |
| `ubuntu216` · RTX 5070 Ti × 4 | 831 | 2 | 15.921 GiB | 2.641 GiB | 16.6% | 110.014 GiB | 75.971 GiB |

VRAM과 RAM 용량은 각 SQLite의 `node_samples` 중 실행 전(`phase='before'`) 스냅샷에서 읽었다. 최소 `MemAvailable`은 성공 실행의 `node_summary_json.memory_available_bytes_min`을 집계했다. 가장 큰 합산 `max_rss_kib`는 33.655 GiB(원격 4 replica)지만, 이는 서로 다른 프로세스의 RSS 합이며 시스템 잔여 RAM과 일대일로 대응하지 않는다. `icns`의 GPU replica 수는 1 또는 2, `ubuntu216`은 1·2·4이고 각 replica는 서로 다른 GPU를 쓴다. 합산 GPU 메모리를 단일 GPU 용량과 비교하면 잘못된 결론이 나온다.

로컬의 9개 성공 모델과 원격의 2개 성공 모델은 **서로 겹치지 않는다**. 같은 모델·입력을 16 GiB와 48 GiB 장치 양쪽에서 성공·실패까지 비교한 기록이 없다. 원격의 Qwen 실행도 시도됐지만 모델 파일을 읽기 전에 실패했으므로 하드웨어 호환성 비교 자료가 아니다.

## 연구·개발 결정

GPU 성공 여부를 라벨로 만들면 모든 학습·평가 행이 양성이다. 항상 `가능`이라고 예측하는 규칙도 관측 자료에서는 정확도 100%가 되므로, 모델의 추가 가치를 평가할 수 없다. 기존 피크보다 작은 VRAM을 가정한 가상 라벨은 측정된 예약량의 임계값일 뿐, 작은 장치에서 실행했을 때의 성공·실패를 증명하지 않는다. 물리 메모리 용량과 사용 가능한 메모리, 프레임워크 예약량, 다른 프로세스 사용량도 다르다.

현재는 등록 시 알려진 모델 크기와 실제 장치 가용 메모리로 보수적인 **호환성 필터**를 두되, 그 결과를 검증된 메모리 추천 모델이라고 주장하지 않는 편이 타당하다. 이 자료에서 학습 가능한 대상은 같은 장치에서의 처리량·지연 또는 관측 메모리 피크 예측이다. 메모리 형상 추천의 효과를 검증하려면 동일 모델·입력을 서로 다른 VRAM/RAM 제한에서 반복 실행하고, 실제 OOM 및 정상 완료 결과를 수집해야 한다. 이 검토에서 새 실행이나 장치 변경은 하지 않았다.
