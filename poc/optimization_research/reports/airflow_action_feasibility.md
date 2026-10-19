# Airflow 단일 실험 원본 DB 수집 게이트

2026-09-28 18:30 UTC. 목적은 [공식 Airflow 계산 산출물](https://zenodo.org/records/2635573)의 조치 사건과 후속 작업 사건을 **실제 한 실험의 PostgreSQL 기록에서** 연결할 수 있는지 확인하는 것이다. 이 문서는 수집 가능성 점검 결과다. `db.tar.gz`를 받거나 PostgreSQL을 복원하지 않았으므로 사건 건수·매칭률은 아직 측정 결과가 아니다.

## 결과를 보기 전 선택한 실험

원본 실험 목록 PDF (`../data/external/airflow_action_feasibility/experiments.pdf`)의 설정표만 보고 `PerformanceFeedback_3_0_5__154`를 선택했다. WL III `3_0` subset 1의 워크플로 5개, PerformanceFeedback/MA5, 오토스케일 간격 15초, 예산 30/0, small/large 자원 각 8개다. [사전 선택 기록](../data/external/airflow_action_feasibility/SELECTION.md)의 SHA-256은 `7c0424fcedee2bcba8979608a3bc09f061cab2cd35bc6c4c4fabde6d342b2b19`이다. 이 선택 뒤에 DB 파일 목록이나 실험 결과를 열지 않았다. 후속 수집 때도 이 디렉터리명과 일치하는 멤버만 추출하고, 결과가 불리해도 다른 실험으로 교체하지 않는다.

## 원본·공간·전송 확인

| 항목 | 검증 결과 |
| --- | --- |
| 공식 Zenodo 메타데이터 (`../data/external/airflow_action_feasibility/zenodo_record.json`) | DOI `10.5281/zenodo.2635573`; 저장 JSON SHA-256 `38ad461c90f3e2fc78a9a9474a5639cbec159ef9583e1510dc00220c88510406` |
| `db.tar.gz` | 공식 크기 **4,179,532,095 B**, 공식 MD5 `ad17be54eca6a1f2d4b964a7518af96d`. 전체 파일은 **미수집·미검산** |
| `experiments.pdf` | 60,012 B. 내려받은 파일 MD5 `1dbeef18281de69261b7af71241279f2`가 공식값과 일치; SHA-256 `a8dc6b7c14c0bfff9e05bf96426a059a9f0706ff8546f2d2b765fffffca9a04e` |
| 원본 README (`../data/external/airflow_action_feasibility/zenodo_README.md`) | 3,138 B. 내려받은 파일 MD5 `ea4137c4118a5532eb5c88fb9ce570ff`가 공식값과 일치; SHA-256 `71bb61862805e7023da2f79598a039a3a7cd6edb0d8caf0bc860a12d893dd57d` |
| 작업 디스크 | `/`의 사용 가능량 **12,066,734,080 B**, `df`의 반올림 사용률 100%. 압축 원본만 저장해도 현재 여유의 34.6%를 차지하며, 선택한 PostgreSQL 디렉터리의 해제 후 크기는 아직 모른다. 같은 디스크에서 다른 학습 작업도 실행 중이다. |
| 공식 파일 범위 읽기 | `Range: bytes=0-16777215`는 HTTP 206, 16,777,216 B, 14.62초, 1,147,198 B/s. `Range: bytes=16777216-25165823`는 HTTP 206, 8,388,608 B, 26.87초, 312,218 B/s. 두 범위는 `/dev/null`로 버렸으며 전체 MD5 검산이나 추출에 사용하지 않았다. |

관측한 두 속도가 유지된다고만 가정하면 4.18 GB 전송에 **약 61~223분**이 걸린다. 두 짧은 범위만으로 전체 전송속도를 확정할 수는 없지만, 현재의 디스크·네트워크·공유 학습 I/O 조건에서는 전체 아카이브와 복원 환경을 동시에 마련하는 비용이 크다.

## 사건 연결 확인 상태

[공식 README](https://zenodo.org/api/records/2635573/files/README.md/content)는 각 실험 디렉터리에 `airflow-autoscaler.log`와 실험 종료 직후의 PostgreSQL 디렉터리가 있고, 논문 관련 자료 대부분이 `stat_log`에 있다고 설명한다. 이전 [공개 자료 감사](public_scale_action_dataset_audit.md)에서 공식 소프트웨어의 `stat_log` 스키마와 사건 유형을 확인했으나, 이번에 선택한 실험의 DB 행은 열지 않았다.

| 확인할 사건 | 한 실험 실제 건수 | 시간·자원 ID 매칭률 |
| --- | ---: | ---: |
| `ResourceAllocated` / `ResourceDeallocated` | 미측정 | 미측정 |
| 같은 자원 ID의 `ResourceIdle` / `ResourceBusy` | 미측정 | 미측정 |
| `TaskEligible` / `TaskStarted` / `TaskFinished` | 미측정 | 미측정 |
| 워크플로 도착과 마지막 `task_instance.end_date` | 미측정 | 미측정 |

후속 단계에서는 선택된 디렉터리만 안전한 경로 검사와 함께 스트리밍 추출하고, 전체 아카이브의 길이·MD5를 확인한 뒤 버전에 맞는 PostgreSQL에서 `stat_log`를 읽는다. `ResourceAllocated/Deallocated`마다 같은 실험·같은 `resource_id`의 후속 상태 사건을 찾고, `TaskStarted/Finished`와 시간순으로 연결한다. 자원 ID가 비거나 같은 시각의 조치가 겹치면 별도 분모와 미매칭 건수를 보고해야 한다. 워크플로 종료는 `dag_run`과 `task_instance`의 키·시각 일치 여부를 검산해야 한다.

**판정: 이번 환경에서는 DB 추출·조인 감사 `NO-GO`; 메타데이터·선택 실험·수집 규칙은 고정.** 조치 건수나 후속 처리량 정답은 아직 없다. 디스크 여유와 전송 조건을 확보하면 위 한 실험의 원본 행에서 매칭률을 측정해 재판정할 수 있다. 이 자료의 자원 전환은 사전 배치된 Airflow worker의 상태 전환이므로, 그 결과를 AI-App GPU VM 생성·삭제의 성능이나 인과 효과로 해석하지 않는다.

## 22:53 UTC 후속 확인: 작은 원본과 DB 압축 앞부분

사전 선택은 그대로 유지했다. [공식 계산 산출물](https://zenodo.org/records/2635573)의 `csv.tar.gz`(6,524,448 B, MD5 `612550cbf2721dbb9a5450331766c225`)와 [공식 소프트웨어](https://zenodo.org/records/2635571)의 `sa.tar.gz`(8,414,212 B, MD5 `b2262a4f5d8b08e5a591cb5344c2e721`)를 내려받아 공식 체크섬과 대조했다. [재현 스크립트](../scripts/audit_airflow_small_artifacts.py)의 출력 (`../data/external/airflow_action_feasibility/small_artifact_audit.json`)은 22개 요약 CSV 어느 곳에도 선택 실험 ID, `timestamp`, `event_type`, `resource_id`, `dag_id`, `execution_date`, `task_id`, `ResourceAllocated`, `ResourceDeallocated` 문자열이 없음을 확인한다. 요약 CSV의 자원 비율·비용·slowdown 배열을 원본 조치 사건으로 역변환할 수 없다.

`db.tar.gz`의 `bytes=0-8388607` 범위만 HTTP 206으로 받아 8,388,608 B를 확인했다(범위 파일 (`../data/external/airflow_action_feasibility/db_tar_gz_range_0_8388607.bin`), SHA-256 `0d48e9b84d4158f917e6f0fb9685ebe1e0680f7eea57e605346be981ba58e162`). 압축된 tar의 이 부분에서 멤버 헤더 **730개**를 보았고, 내용을 끝까지 읽은 멤버는 **729개**다. 마지막 멤버는 범위 끝에서 잘린다. **111개 실험 디렉터리의 파일이 섞여 있다.** 선택 실험에서 완전히 읽힌 멤버는 PostgreSQL의 `_fsm`·`_vm` 보조 파일 5개뿐이며 선택 실험의 `airflow-autoscaler.log`와 사건 테이블 자료는 이 범위에 없다. 이 8 MiB만으로 실제 조치 건수나 연결률을 계산하지 않았다. Zenodo가 공개한 파일 목록에는 실험별 개별 다운로드 또는 gzip 임의 접근용 인덱스도 없다. 전체 DB를 디스크에 풀 필요는 없지만, 선택 실험의 모든 멤버를 안전하게 찾으려면 압축 스트림을 끝까지 **전송·순차 해제하면서** 다른 실험을 건너뛰어야 한다.

공식 소프트웨어의 `airflow/airflow/autoscalers/resource_allocator.py`는 `ResourceAllocated`를 기존 자원을 `DOWN→BOOTING`으로 바꿀 때 기록하고, `ResourceDeallocated`를 유휴 자원을 `IDLE→DOWN`으로 바꿀 때 기록한다. 따라서 할당 시각은 사용 준비 완료 시각이 아니다. `airflow/airflow/jobs.py`의 `TaskStarted`는 작업을 자원에 배정해 큐에 넣는 시각에 기록된다. 실제 작업 완료는 `TaskFinished`와 `task_instance.end_date`로 확인해야 한다. `models.py`에서 `WorkflowFinished` 사건은 사용하지 않는다고 명시돼 있으므로, 완료 워크플로는 `dag_run`과 마지막 `pause` 작업의 `task_instance.end_date`를 연결해 확인한다. 작업 사건에는 `resource_id`, `dag_id`, `execution_date`, `task_id`가 있어 **원본 행을 확보하면** 자원별 배정·완료와 워크플로 결과의 사실적 시간 관계를 검산할 수 있다. 직접적인 조치 ID나 실행하지 않은 대체 조치의 결과는 없다.

현재 빈 디스크는 약 11 GiB이고 PostgreSQL 실행 도구가 설치돼 있지 않다. 8 MiB 범위 전송은 약 27초였으나 이전 두 범위의 속도와 차이가 커 전체 4.18 GB 전송 시간을 단일 속도로 확정하지 않는다. **이번 게이트 결과는 여전히 `NO-GO`다:** 실제 `ResourceAllocated/Deallocated` 수, 같은 자원 ID의 후속 `ResourceIdle/Busy` 수, 작업·워크플로 연결률은 모두 미측정이다. 후속 수집 시 선택한 `PerformanceFeedback_3_0_5__154`만 스트리밍 추출하되, 전체 다운로드 길이·MD5와 선택 디렉터리의 모든 멤버를 검증한 뒤 DB를 복원해 사건별 분모를 보고해야 한다.
