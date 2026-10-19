# 공개 AI 추론 오토스케일 자료 추가 감사

2026-09-28 기준. [기존 공개 자료 감사](public_scale_action_dataset_audit.md)와 [1GB 이하 자료 감사](public_subgigabyte_action_outcome_audit.md)를 보완한다. **요청별 결과와 발행된 스케일 명령·준비 완료 시각을 같은 실행에서 연결할 수 있는 자료는 이번에도 확인하지 못했다.** 아래의 상태 변화는 관측값이며, 명령 이벤트나 조치의 인과 효과가 아니다.

| 순위·자료 | 직접 확인한 필드와 범위 | 판정 |
| --- | --- | --- |
| 1. [HAS-GPU 공식 아티팩트](https://zenodo.org/records/15596511), `HAS-GPU.zip` 62,262,205 B | 18개 AI 추론 실행 폴더의 요청 CSV 61,021행: `duration,mode,status_code,success,timestamp`. 같은 폴더의 약 2초 간격 GPU CSV 2,633행: 대개 `timestamp,node_name,gpu_id,sm_usage,quota_usage,pod_count`. 60,791개 요청의 완료 시각이 해당 실행의 GPU 관측 구간 안에 있다. 연속 관측 사이 quota 변화는 47회. | **조건부 GO: 사전 시점 정보로 다음 구간의 요청 지연을 예측하는 탐색 실험. NO-GO: `SCALE_OUT/IN` 명령 학습·효과 추정.** |
| 2. [µOpt 공식 재현 패키지](https://zenodo.org/records/17407219), ZIP 61,599,985 B | 6개 실험의 분 단위 `Replicas.csv`와 초 단위 Locust `*_stats_history.csv`가 같은 실행에 있다. | **NO-GO: 명령 발행·Ready 이벤트가 없으며 AI 추론 자료가 아니다.** 관측 replica 수와 후속 부하·지연의 결합 후보에 한정한다. |
| 3. [ScalerEval 공식 저장소](https://github.com/WHU-AISE/ScalerEval), `tmp/result.zip` 3,816,238 B | 12개 Hipster/Sockshop 실행의 `record.csv`에 시각·서비스별 pod 수·QPS·p95, Locust 기록에 사용자 지연이 있다. | **NO-GO: 명령 발행·Ready 이벤트가 없으며 AI 추론 자료가 아니다.** |
| 4. [2026년 예측 오토스케일 재현 패키지](https://zenodo.org/records/18709933), `Archive.zip` 269,503,080 B | 표본 원본 CSV의 `timestamp,response,pods,watt,request_count`; 다른 실험 CSV에는 `error,model`도 있다. | **NO-GO: 관측 pod 수와 응답만 확인했고 발행 명령·Ready 이벤트를 확인하지 못했다.** |

## HAS-GPU의 정확한 사용 범위

HAS-GPU의 17개 실행에서 `pod_count`는 모든 관측 시점에 `1`이다. 나머지 `2-resnet` 실행의 GPU CSV에는 `pod_count` 대신 `pod_name,function_name,sm_partition,quota`가 있다. 따라서 47회는 **quota의 관측 변화**이지, 수평 스케일 조치 47건이 아니다. `has-run-test.py`는 시험 시작 전 Pod Ready를 기다리지만, 저장된 CSV에는 개별 조치의 명령 ID·발행 시각·후속 Ready 시각이 없다. 요청 61,021건은 모두 `status_code=200`으로 기록돼 실패 위험 분류도 이 자료에서 검증할 수 없다.

`validation/client/test_batch.py`의 요청 기록 코드는 HTTP 응답을 받은 **뒤** `datetime.now().isoformat()`을 `timestamp`로 쓴다. 판단 시점의 입력을 만들 때 요청 시작 시각을 근사하려면 `timestamp - duration`을 계산해야 한다. 요청 시각은 시간대가 없는 문자열이고 GPU 관측 시각은 `Z`가 붙어 있으므로 실행별 시계·시간대 일치도 먼저 검사해야 한다. GPU CSV의 quota가 특정 요청에 실제로 적용된 시각은 이 자료만으로 알 수 없다.

사전에 고정할 탐색 과제 후보는 **실행별 과거 요청 도착률·지연과 마지막으로 관측된 quota/SM 값으로 10~30초 뒤 요청 지연의 p95를 예측**하는 것이다. 요청 시작 시각을 복원하고, 목표 구간의 최소 요청 수와 입력의 시각 유효성 조건을 먼저 정한다. 최근 구간 지연 유지값·과거 중앙값을 기준선으로 두고, 실행 전체 또는 모델 계열 전체를 분리해 평가한다. quota와 지연의 관계는 부하에 따라 동시에 바뀌므로 예측 성능을 스케일 정책의 이득이나 대체 quota의 효과로 해석하지 않는다. 이 후보는 본 감사에서 전체 자료의 구조와 성공률을 이미 확인한 **사후 선택**이다. 같은 18개 실행을 나눠 얻는 결과는 독립 외부 검증이 아니며, 새로운 실행에서 재검증해야 일반화 주장이 가능하다.

## 확인 방식과 남은 한계

공식 압축 파일에서 HTTP Range로 ZIP 목차와 선택 멤버만 받아 헤더·행을 직접 읽었다. HAS-GPU의 위 18쌍 CSV는 멤버별 압축 해제 크기와 CRC를 확인했다. 전체 ZIP은 다운로드하지 않았으므로 게시된 전체 MD5는 검증하지 않았다. [HAS-GPU 공식 Zenodo API](https://zenodo.org/api/records/15596511)의 `metadata.license.id`는 `cc-by-4.0`이다. 원본을 재사용할 때 저작자 표시와 출처 명시 조건을 지킨다.

실제 배포·스케일 판단 연구에는 [사건 데이터 계약](scale_effect_data_contract_v1.md)의 `decision_id/action_id`, 명령 발행과 Ready 시각, 동일 앱의 요청 결과가 여전히 필요하다.
