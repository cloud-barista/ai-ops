# 공개 자료의 스케일 조치·결과 연결 가능성 감사

2026-09-28 기준. 기존 [SQLite 재점검](local_signal_refresh_2026_09_28.md)에는 실제 스케일 명령과 그 전후 사용자 결과가 없다. 아래는 공개 자료의 **기록된 조치와 후속 관측값**을 한 실험 안에서 연결할 수 있는지 조사한 결과다. 새로운 모델 성능이나 조치의 인과 효과를 주장하지 않는다.

| 후보 | 확인한 필드와 크기 | 판단 |
| --- | --- | --- |
| Google [2011](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)·[2019](https://github.com/google/cluster-data/blob/master/ClusterData2019.md) ([현지 감사](google_2011_resource_feasibility.md)) | 작업 사건·배정·CPU/RAM 사용량. 사용자 AI 응용 지연·GPU SLO와 대체 VM 형상의 결과가 없다. | CPU/RAM **기존 배정 아래의 관측 예측**에는 사용했으나, AI-App `SCALE_OUT/IN` 효과의 정답으로 사용할 수 없다. |
| [Ghent gym-hpa 관측 CSV](https://zenodo.org/records/7944661) | Online Boutique 8,680,050 B, 28,785행·78열; Redis 7,180,695 B, 100,000행·15열. `date`, 서비스별 `num_pods`, `desired_replicas`, CPU·메모리 사용량, `traffic_in/out`, `latency`. Zenodo는 비상업용 `other-nc`로 표시한다. | 공개 CSV에 action ID·명령 시각·실험 구간/정책 ID가 없다. [공개 코드](https://github.com/jpedro1992/gym-hpa/blob/main/gym_hpa/envs/deployment.py)의 `desired_replicas`는 CPU·메모리와 목표 사용률로 **계산한 권장값**이므로 실행 명령으로 해석하면 안 된다. 코드가 모든 서비스 `latency` 열에 같은 애플리케이션 지연을 기록한다. Online Boutique의 시간순 단일 서비스 pod 수 전이는 14,037회이나 전후 60초에 다른 pod 수 전이가 없는 사례는 3회, 120초는 0회였다. 전후 지연 차이를 특정 조치의 효과로 학습할 근거가 약하다. |
| [Airflow 성능 피드백 오토스케일 실험](https://zenodo.org/records/2635573) | `db.tar.gz` 4,179,532,095 B(실험별 PostgreSQL DB와 `airflow-autoscaler.log`), `csv.tar.gz` 6,524,448 B(추출된 자원·비용·워크플로 slowdown), `experiments.pdf` 60,012 B. 자료 CC BY 4.0. [소프트웨어](https://zenodo.org/records/2635571) `sa.tar.gz` 8,414,212 B, Apache 2.0. | **조치와 후속 결과를 연결할 스키마가 있는 가장 가까운 후보.** 공개 코드의 `stat_log`에는 `timestamp`, `event_type`, `resource_id`, `resource_type_id`, `resource_type_name`, `user_id`, `dag_id`, `execution_date`, `task_id`가 있으며 `WorkflowArrived=0`, `TaskStarted=3`, `TaskFinished=4`, `ResourceAllocated=5`, `ResourceIdle=6`, `ResourceBusy=7`, `ResourceDeallocated=8`, `TaskEligible=11`을 구분한다. `dag_run.execution_date`와 마지막 `task_instance.end_date`로 워크플로 응답시간을 계산할 수 있다. 사건·결과의 실제 매칭률은 미측정이며, 자원 조치와 개별 워크플로 사이의 직접적인 action ID도 없다. |

Ghent의 파일 크기·MD5를 [Zenodo 레코드](https://zenodo.org/records/7944661)의 원본과 대조한 뒤 헤더와 시계열을 메모리에서 확인했다. Redis는 79,505개 고유 초/약 43.9시간이며 동일 시각 행이 20,495개다. 모든 서비스의 `latency` 열이 행마다 같은 값인 것도 확인했다. `num_pods`의 변화는 **관측된 상태 변화**일 뿐 조치 발행 기록이 아니다.

Airflow에서는 작은 `experiments.pdf`, `csv.tar.gz`, [소프트웨어 원본](https://zenodo.org/records/2635571)을 해시와 대조해 열었다. [원 논문](https://arxiv.org/pdf/1905.10270)은 동일한 합성 워크플로 부하를 여러 autoscaler로 실행한 물리 클러스터 실험이다. 다만 스케일 조치는 이미 배치된 Celery worker의 예약 상태를 중앙 DB에서 `DOWN→BOOTING` 또는 `IDLE→DOWN`으로 바꾸며, CSP VM 생성·삭제 명령은 아니다. [선택 실험의 공식 요약과 DB 앞 8MiB](airflow_action_feasibility.md)에서도 사건 행은 확인되지 않았다. 실제 이벤트 수와 매칭률을 확인하지 않았으므로 **스키마상 연결 가능** 단계로 둔다. 전체 압축 스트림 4.18GB를 읽어야 선택 실험을 확보할 수 있다.

## 제한된 후속 실험 제안

Airflow를 쓴다면 하나의 실험 디렉터리만 스트리밍 추출·복원해 `ResourceAllocated/Deallocated` 시각과 동일 자원 ID의 `ResourceIdle/Busy`, `TaskStarted/Finished`, 워크플로 도착·종료 매칭률을 먼저 확인한다. 이 게이트를 통과하면 실험·사용자·60초 간격 단위로 **실제로 실행된** 증감과 그 후 1분의 대기 작업 수·작업 처리량을 사실적 예측 대상으로 삼을 수 있다. 대기 작업 수는 `TaskEligible`에서 `TaskStarted`를 뺀 누적 차이로 정의하고, 결과 창이 끝까지 기록된 경우만 쓴다. 입력은 결정 시각 이전의 이벤트·예산·자원 종류만 사용한다. 같은 워크플로 세트를 다른 정책에 반복한 실험은 정책 단위 비교에만 쓰고, 한 시점의 미실행 대체 조치 결과로 취급하지 않는다. 학습·선택은 WL I의 `1_0/1_1`·`1_2` 전체 실행, 독립 시험은 WL II의 `4_0/4_1/4_2` 전체 실행으로 분리하는 안이 타당하다. 현재 대기열 지속·최근 평균·단순 수요/가용량 계산을 강한 기준선으로 두고 실행별 오차도 제시한다. 이 실험은 **워크플로 자원 예약의 관측 예측**으로 한정한다. AI 응용의 GPU VM 스케일 정책, 사용자 지연 SLO 및 대체 조치 효과의 검증에는 별도 KHU·INNO·ETRI 실행 로그가 필요하다.

**결론:** 공개 Google 자료나 소형 gym-hpa CSV를 실제 AI-App 스케일 정책의 정답으로 학습하지 않는다. Airflow는 명시적 예약 이벤트와 실행 결과가 같은 실험에 남은 교육·방법 검증 후보지만, 원본 DB 매칭률 확인과 도메인 한계 표시가 선행되어야 한다.

## 2026-09-29 추가 확인: 조치→Ready→요청 결과

| 후보 | 공개 원본에서 확인한 내용 | 획득 판단 |
| --- | --- | --- |
| [MPC vs HPA Kubernetes 실험](https://github.com/vshulcz/mpc-autoscaler/blob/main/docs/RESULTS.md) ([Apache 2.0](https://github.com/vshulcz/mpc-autoscaler/blob/main/LICENSE)) | 실제 Pod Ready 지연 약 40초와 HTTP toy workload의 200 rps spike 실험을 보고한다. 공개 `RESULTS.md`는 5.3KB 요약으로, HPA/MPC 한 쌍의 p95·p99·성공률과 실행 디렉터리 경로를 담는다. [공식 evidence 페이지](https://vshulcz.github.io/mpc-autoscaler/experiments.html)는 원시 실행 기록이 Git 밖에 있다고 명시한다. 조치 발행·Pod Ready·개별 요청 지연을 같은 공개 행에 연결할 수 없다. | **보류.** 결과는 단일 비-AI HTTP 실험 요약이며 원시 조치/요청 자료의 공개 크기·필드를 확인할 수 없다. |
| [Firecracker sandbox GCP 스케일 실험](https://github.com/ayush6624/sandbox/blob/main/docs/autoscaling-latency.md) | [공개 JSON 1.59KB](https://github.com/ayush6624/sandbox/blob/main/sdk/typescript/benchmarks/results/suspended-standby-2026-07-24.json)에 첫 sandbox 생성 요청·MIG resize 관측·worker 구동·전체 완료 시각과 160개 생성의 합산 지연/성공 건수가 있다. 문서상 다른 실험의 `tests/results/autoscale-20260727-legacy-held-final/`에는 `timeline.jsonl`·`burst.json`이 있으나, 2026-09-29 현재 그 GitHub 공개 경로는 404였다. 저장소의 라이선스 파일/메타데이터도 확인되지 않았다. | **보류.** 검증 가능한 원본은 집계 1건이며 sandbox 생성 결과다. AI-App 요청 지연, 개별 요청 행, 정확한 resize 명령 시각과의 연결은 없다. |

두 자료 모두 1GB 미만의 공개 **완전한** 조치→Ready→사용자 요청 결과 데이터로 확인되지 않았다. 새 지도학습 자료로 내려받지 않는다. 특히 sandbox 문서도 `mig_target` 시각이 폴링 관측이라 실제 resize 명령 시각의 상한이라고 구분한다.

## 2026-09-29 추가 확인: 요청 원본이 있는 공개 실행

공개 실행의 각 파일이 **같은 실행**에 속하는지를 기준으로 다시 살폈다. [inference-platform의 scale-out 실행](https://github.com/kornsour/inference-platform/tree/main/phase-2-capstone/local/runs/2026-08-27-scale-out)은 `events-successfulrescale.yaml`에 목표 크기 5의 스케일 사건을, `scale-demo-output.md`에 replica·TTFT p95의 약 6초 간격 요약을 담지만, 그 실행의 요청별 결과와 Pod Ready 사건을 함께 제공하지 않는다. [별도 game-day 실행](https://github.com/kornsour/inference-platform/tree/main/phase-2-capstone/local/runs/2026-08-27-gameday)은 요청별 `loadgen.log`와 Ready를 관찰하는 `pods-watch.log`가 있으나 직접 Pod 삭제 실험이고, mock-vLLM을 사용한다. 두 실행의 파일을 한 조치의 전후 결과로 합치지 않는다.

[llm-d 공식 벤치마크 결과](https://github.com/llm-d/llm-d-autoscaling/tree/main/benchmark/results)는 요청별 수명 기록이 포함된 압축 실행물을 공개하지만, 확인한 실행 디렉터리에는 대응하는 스케일 명령·Ready 원본 기록이 없다. 기존 [HAS-GPU 자료](public_ai_autoscaling_dataset_followup.md)는 요청 시각과 quota·pod 수 관측이 연결되지만 조치 발행·Ready가 없고, [H100/vLLM 시작 실험](public_llm_startup_trace_feasibility.md)은 실제 시작→health→첫 토큰 200건을 이미 단계별 준비시간 연구에 사용했다. 후자는 지속 추론의 `SCALE_OUT/IN` 정답이 아니다. 이 추가 점검에서도 **명령 발행·준비 완료·요청별 AI 성과를 같은 실행에서 연결할 새 학습 자료는 확인되지 않았다.**

## 2026-09-29 후속 원본 검색: 새 학습 자료 판정

| 후보 | 원본에서 확인한 사건·결과 | 판정 |
| --- | --- | --- |
| [Faro 공식 저장소](https://github.com/beomyeol/faro) | 실제 IBM Kubernetes ML 추론 실험의 실행 절차와 NCSA 라이선스가 공개돼 있다. 절차는 실행 중인 Pod에서 `run.log`와 Ray Serve 로그를 추출해 파싱하고 `latency_stats.pkl`을 만든다. 공개 `results`에는 이 원시 조치·요청 로그의 동일 실행 결합물이 확인되지 않았다. | 조치→Ready→요청별 결과의 공개 학습 자료로 **보류**. 집계 점수를 원시 사건으로 역변환하지 않는다. |
| [GPU scale-to-zero 추론 데모](https://github.com/adityonugrohoid/gpu-autoscale-inference) | 저장소는 MIT 라이선스이며 실제 GPU·vLLM의 전체 주기 로그 생성 절차를 설명한다. README에 각 실행의 시각별 로그를 `gitignored; not committed`라고 명시한다. | 원시 로그가 공개 저장소에 없어 **보류**. |
| [Eprecisio L4/vLLM 실험](https://www.eprecisio.com/blog/vllm-autoscaling-signal-kubernetes-hpa) | 요청별 기록·GPU·vLLM 계측은 같은 시계로 측정됐다고 설명하지만, 게시자는 실험 저장소가 비공개이고 원본은 별도로 요청해야 한다고 명시한다. | 현재 접근 가능한 공개 학습 자료로 **보류**. 게시물의 집계값으로 조치별 정답을 만들지 않는다. |

앞 절의 `inference-platform`도 실제 AI 추론 GPU가 아닌 mock 엔진이며, 스케일 사건이 있는 실행과 요청별 결과·교체 Ready가 있는 수동 Pod 삭제 실행은 서로 다르다. 이번 검색에서 추가로 얻은 **동일 실행의 발행 조치 시각·Ready·요청별 AI 사용자 결과**를 갖춘 자료는 0개다. 이 결과는 공개 자료의 전수 부재를 증명하지 않으며, 위 출처와 해당 시점에 확인한 공개 아티팩트의 적격성 판정이다. 실제 스케일 효과 모델은 [사건 데이터 계약](scale_effect_data_contract_v1.md)의 결합 기록이 생길 때 시작한다.
