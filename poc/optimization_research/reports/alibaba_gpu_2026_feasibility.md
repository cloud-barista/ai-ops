# Alibaba GPU v2026 추적의 경희대 자원 요구 분석 적합성

점검일: 2026-09-28. **판정: 좁은 GPU/CPU 사용 위험 연구의 자료 후보이며, AI 응용의 최소 CPU·RAM·GPU 형상 추천 정답은 아니다.** 대용량 파일을 내려받거나 모델을 학습하지 않았다. 공식 [자료 설명](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/README.md), [스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md), [다운로드 목록](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md)을 대조하고, 공개 OSS 객체에 `HEAD`·HTTP 바이트 범위 요청만 보냈다.

## 저장 규모와 가장 작은 실험 단위

| 공식 ZIP | 객체 크기 | 확인한 내부 조각 |
| --- | ---: | --- |
| `asi_opensource_pod_hourly.zip` | 351,803,513,445 B | 185일×24시간 = **4,440개** Parquet. 가장 작은 `day=5/hour=06`은 37,298,863 B, 중앙 조각 크기는 80,700,359 B. |
| `asi_opensource_server_hourly.zip` | 3,080,121,387 B | 같은 4,440개 시간별 Parquet. 가장 작은 조각은 570,841 B. |
| `asi_opensource_job_execution_summary.zip` | 1,188,295,031 B | Parquet 하나. CPU·GPU 메모리의 시간별 사용량이 없어 이 파일만으로 요구 분석을 할 수 없다. |

세 객체의 응답은 문서의 크기와 일치했고 `Accept-Ranges: bytes`였다. ZIP 중앙 디렉터리만 읽어 조각 위치·길이를 알아낸 뒤 ZIP 내부 Parquet의 footer와 일부 열을 범위 요청으로 읽었다. 전체 ZIP이나 개별 Parquet를 저장하지 않았다. `pod_hourly` ZIP의 중앙 디렉터리 조회는 약 512 KB/4범위 요청이었다. 이 방식이면 351.8 GB 전체를 먼저 내려받지 않고 시각 몇 개를 실험할 수 있다.

**가장 작은 조각은 학습 표본으로 부적합하다.** `day=5/hour=06` Parquet는 1,123,104행·14 row group이지만, 모든 group의 footer에서 `gpu_request`, `gpu_mem_request`, `cpu_request_cores`의 최소·최댓값이 전부 0이다. `avg_cpu_request_util`, `avg_memory_util`은 물리형이 `Null`이었다. 같은 조각에는 GPU 사용량 열이 있어 요청·사용량 연결을 평가하기 어렵다. 반면 `day=0/hour=00` 조각은 63,791,770 B·1,179,029행이며 요청값에 변동이 있다. 인접 `hour=01`은 65,677,291 B·1,212,189행이다. 후속 2시간 실험의 두 pod 조각은 합계 129,469,061 B이고, 필요한 열·row group만 범위 요청으로 읽으면 전송량을 줄일 수 있다.

## 실제 작은 표본에서 확인한 결합

대표 `day=0/hour=00` pod 조각의 **첫 row group 71,963행만** 읽었다. 이 행은 117만 행 전체의 무작위 표본이 아니다. 그중 `gpu_request > 0`은 5,385행, `job_type_public=online_inference`인 GPU 요청 pod는 3,580행, 여기에 `model_type_public=genai`까지 적용하면 788행이다. 마지막 788행 중 652행은 CPU/GPU/GPU 메모리 요청과 GPU SM·GPU 메모리·CPU/메모리 사용 필드가 모두 결측 없이 있다. 온라인 GPU pod 전체에서는 2,552/3,580행이 이 일곱 필드를 모두 갖는다.

같은 첫 row group의 온라인 GPU pod 3,580행 중 `workload_id`가 있는 행은 **1,812**이며, GenAI 온라인 GPU 788행 중에는 **386**행이다. `pod_id`는 모든 행에 있으므로 pod별 시계열은 더 넓게 연결할 수 있지만, 같은 응용의 여러 pod를 묶는 `workload_id` 분석에서는 결측을 별도 처리해야 한다. 공개 `job_type_public`은 `training/online_inference/offline_inference/...`, `model_type_public`은 `genai/rec/cv/embedding/...` 같은 **범주**이고 실제 모델명·응용 설명은 없다. [공식 필드 정의](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md).

같은 `day=0/hour=00` 서버 조각에는 고유 서버 **28,036개**가 있다. 읽은 pod 첫 row group 71,963행 전부가 그 시간의 서버 조각 `server_id`에 결합됐고, GPU 요청 pod 5,385행도 전부 결합됐다. 서버 표는 GPU 모델·GPU 개수·CPU 코어 용량을 제공하지만 **서버 RAM 용량은 없다**. 인접 `day=0/hour=01`의 첫 row group 71,672행도 읽었다. 두 첫 row group 사이에 같은 익명 `pod_id` **3,077개**, 결측을 뺀 같은 `workload_id` **4,706개**가 나타났다. 이 숫자는 첫 row group끼리의 겹침만 세었으므로 전체 시간 간 연속 pod·workload 수의 하한일 뿐이다. 다만 상대 `day/hour`와 안정적인 익명 ID를 이용한 시간순 연결이 실제로 가능함을 확인했다.

## 학습 목표와 누출 경계

공개 pod 행에는 `gpu_request`, `gpu_mem_request`, `cpu_request_cores`, `used_gpu_hours`, `avg_gpu_sm_util`, `avg_gpu_mem_gib`, `avg_cpu_request_util`, `avg_memory_util`, 준비 여부와 일부 지연이 **동시에** 있다. 따라서 온라인 AI 범주에서 *현재 요청 형상·과거 사용량으로 다음 시간의 GPU 사용량 또는 과부하 위험을 예측*하는 작은 예비 실험은 설계할 수 있다. 같은 시간의 사용량으로 그 시간의 과부하를 예측하면 누출이므로 특징의 최대 가용 시각을 정답 시간보다 앞에 둔다. 지속 예측과 유형별 과거 빈도를 기준선으로 두고, 날짜·pod/workload를 분리해 점수화해야 한다. 이 목표는 경희대 배포·스케일링 판단에 공급할 **위험 신호** 수준이다.

요청 자원 수치는 기존 운영자가 선택한 **배정 요청**이다. 실행 가능한 최소 CPU/GPU 수량이나 다른 형상에서의 사용자 SLO 결과가 아니다. `gpu_mem_request`는 문서상 출처가 보고한 가속기 메모리 요청이며 단위와 집계 범위를 추가 확인하기 전에는 `avg_gpu_mem_gib`와 직접 뺄 수 없다. `avg_gpu_sm_util`은 첫 row group에서 785를 넘는 값도 있어 이를 단일 GPU의 0~100% 사용률로 읽지 않는다. `avg_memory_util`에도 음수 관측이 있으므로 범위·결측·물리 의미 감사가 먼저 필요하다. 가장 작은 시간 조각처럼 요청 필드가 모두 0인 구간을 무조건 정상 배정으로 학습하면 잘못된 신호가 된다.

**RAM 요청량과 절대 RAM 사용량, 실제 모델명/파라미터 크기, 사용자 요청 지연·처리량 SLO, 다른 자원 형상의 반사실 성능은 공개 스키마에 없다.** 따라서 이 자료로 자연어 응용 설명→CPU/RAM/GPU 요구 형상 지도학습, 최소 VM RAM 예측, 배포 승인 또는 Scale-out/in 행동의 효과를 검증할 수 없다. `job_execution_summary`에는 준비·스케줄 지연과 실행 기간이 있지만 시간별 CPU/GPU 사용량이 없고 실제 배포·축소 명령 결과도 아니다. [공식 스키마](https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md).

## Google 2011과 우선순위

[Google 2011 공식 자료](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md)는 29일·약 12,500기계의 Borg 셀 자료로 압축 크기가 약 41 GB다. Alibaba GPU v2026은 훨씬 크지만 GPU 모델·요청·사용량과 AI 작업·모델 종류 범주를 같은 시간별 pod 행에 공개하고, ZIP 내부 조각을 바이트 범위로 읽을 수 있다. **GPU 사용 위험이라는 좁은 경희대 연구에는 Alibaba가 더 직접적인 후보**다. 반면 CPU·RAM·GPU의 최적 형상 정답, 특정 AI 모델 일반화, 사용자 SLO 기반 행동 효과를 얻는 목적에는 두 공개 추적 중 하나를 바로 대체 정답으로 선정해서는 안 된다.

다음 단계가 필요하면 대표 2~3개 연속 시간만 대상으로 필드 품질과 pod 연속성 게이트를 먼저 정한다. 이번 점검에서는 새 모델 학습과 운영 판단을 수행하지 않았다.
