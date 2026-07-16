# 1차년도 VM 통합·개별 동작 시나리오 초안

## 1. 목적

이 문서는 1차년도 **VM-only(컨테이너 제외)** 통합 시험을 논의하기 위한 초안입니다. 기존 `data/ops_llm_eval_scenarios.jsonl`은 후보 LLM의 판단 품질을 비교하는 평가 문제이며, 본 문서의 동작 시나리오와 목적이 다릅니다.

| 구분 | 목적 | 결과 |
| --- | --- | --- |
| Ops LLM 평가 시나리오 | 동일한 운영 문제에 대한 LLM 응답 품질 비교 | 모델별 점수와 선정 근거 |
| VM 동작 시나리오 | 기관별 기능이 실제 VM 흐름에서 연결되는지 확인 | 단계별 요청·응답, 실행 상태, 통합 성공 여부 |

## 2. 1차년도 시험 범위

### 포함

- Go 기반 service-control 판단 및 API/CLI 실행
- 에이전트 등록 상태와 허용 Action 검증
- workload 요구조건 기반 CPU/GPU VM 배치 판단
- VM 배포·제어 명세 생성
- CB-Tumblebug을 통한 CPU/GPU VM 생성과 상태 확인
- SSH 또는 원격 실행 API를 통한 VM 내 AI 응용 실행
- 프로세스 상태, 실행 로그, GPU 인식 결과 확인

### 제외

- Docker image 기반 배포
- Kubernetes, Pod, Deployment, replica 제어
- 컨테이너 오케스트레이션
- 무인 운영 수준의 완전한 폐루프 자동 제어

## 3. 참여 구성요소와 책임

| 구성요소 | 1차년도 책임 |
| --- | --- |
| AI 응용 등록·배포 프로토타입 | AI 응용 파일과 버전, 실행 조건을 등록하고 배포 요청 생성 |
| Go service-control prototype | 에이전트·Action 검증, CPU/GPU VM 배치 판단, 배포·제어 명세 생성 |
| AI-Infra / CB-Tumblebug | AWS CPU/GPU VM 생성·조회·삭제와 접속 정보 제공 |
| AWS Ubuntu VM | AI 응용을 호스트 프로세스로 실행하고 상태·로그 제공 |
| 시험 운영자 | 연계되지 않은 단계의 요청 전달, SSH 실행, 결과 확인 |

## 4. 통합 동작 시나리오

### 시나리오 I-01: GPU AI 응용의 VM 배치·실행·상태 확인

**목적:** GPU가 필요한 AI 응용의 등록 정보가 service-control 판단을 거쳐 적합한 AWS GPU VM에서 실행되고, 실행 상태가 확인되는 전체 흐름을 검증합니다.

**사전조건:**

- 컨테이너가 아닌 실행 파일 또는 스크립트 형태의 시험용 AI 응용이 준비되어야 합니다.
- AWS credential과 CB-Tumblebug 연결이 검증되어야 합니다.
- CPU/GPU VM 자원 정보와 workload SLO가 준비되어야 합니다.
- 생성된 VM에 SSH 또는 원격 명령으로 접속할 수 있어야 합니다.

| 단계 | 담당 | 입력 | 동작 | 출력 | 현재 상태 |
| --- | --- | --- | --- | --- | --- |
| 1 | AI 응용 등록·배포 | 응용 파일, 버전, 실행 명령 | AI 응용과 실행 정보를 등록 | 응용 식별자와 실행 명세 | 기관 간 계약 협의 필요 |
| 2 | Service-control | workload, accelerator, VRAM, latency, throughput | 배치 판단 입력을 검증 | 정규화된 workload 요구조건 | Go 설정/API로 구현됨 |
| 3 | Service-control | agent, proposed Action, 대상 자원 | 등록 상태와 bounded Action을 검증 | approved 또는 rejected와 사유 | Go prototype 구현됨 |
| 4 | Service-control | workload와 CPU/GPU VM 후보 | 부적합 자원 제외 후 latency·throughput·cost·capacity 점수 계산 | 선택 VM profile과 판단 근거 | Go prototype 구현됨 |
| 5 | Service-control | 선택 VM profile과 제어 Action | VM 배포·제어 명세 생성 | VM 수, 자원 조건, 허용 Action, SLO | Go prototype 구현됨. VM-only 필드 보완 필요 |
| 6 | AI-Infra / CB-Tumblebug | VM 생성 요청 | AWS Ubuntu CPU/GPU VM 생성 | VM ID, 상태, public IP, SSH key 정보 | 외부 인프라에서 실행 검증됨. 자동 API 연계 필요 |
| 7 | AI 응용 등록·배포 | 응용 파일, 실행 명령, 접속 정보 | 파일 전송 후 VM host process로 실행 | PID 또는 실행 작업 ID | 수동 SSH 절차 필요 |
| 8 | VM / AI-Infra | 실행 작업 ID | 프로세스, 로그, `nvidia-smi` 상태 확인 | running/failed, 로그, GPU 사용 정보 | 수동 확인 가능. 상태 API 계약 필요 |
| 9 | Service-control | 실행 상태와 metric | 결과를 기록하고 후속 Action 후보 판단 | 상태 요약 또는 허용된 제어 계획 | 상태 피드백 연계 미구현 |

**통합 성공 기준:**

1. GPU workload가 CPU-only VM에 배치되지 않아야 합니다.
2. 등록되지 않았거나 허용되지 않은 Action은 실행 단계로 전달되지 않아야 합니다.
3. 선택한 VM profile과 실제 생성된 VM의 accelerator 조건이 일치해야 합니다.
4. AI 응용이 컨테이너 없이 VM host process로 실행되어야 합니다.
5. 실행 상태, 로그, GPU 인식 결과를 하나의 시험 기록으로 남겨야 합니다.

## 5. 개별 동작 시나리오

### 시나리오 U-01: 에이전트 등록·Action 검증

**목적:** 등록된 에이전트만 자신의 허용 범위 안에서 제어 Action을 제안할 수 있는지 검증합니다.

| 시험 입력 | 기대 결과 |
| --- | --- |
| 활성 에이전트 + 등록된 Action | `approved=true`와 대상 파라미터 반환 |
| 활성 에이전트 + 미등록 Action | `rejected`와 거부 사유 반환 |
| 미등록 또는 비활성 에이전트 | 실행 단계 진입 차단 |

대표 구현은 `config/agent_registry.json`, `go/service-control-api`, `go/aiops-guard`에서 확인합니다. 단, standalone Go Guard를 service-control runtime에서 직접 호출하는 최종 wiring은 별도 연계 항목입니다.

### 시나리오 U-02: CPU/GPU VM 배치 판단

**목적:** AI workload 요구조건에 따라 적합한 CPU/GPU VM을 선택하거나, 적합한 자원이 없을 때 배치를 거부하는지 검증합니다.

| 시험 입력 | 기대 결과 |
| --- | --- |
| GPU 필요, 예상 VRAM 16GB, latency SLO 100ms | NVIDIA L4급 GPU VM 선택과 점수·근거 반환 |
| accelerator 불필요 경량 분류 workload | 조건을 만족하는 CPU VM 선택 가능 |
| 예상 VRAM이 모든 GPU 후보 용량보다 큼 | `manual_review_required`와 자원별 제외 사유 반환 |
| 가용 VM 수가 0인 후보 | 해당 후보를 배치 대상에서 제외 |

대표 구현은 `config/inference_optimization.json`과 `go/service-control-api/internal/api/service.go`에서 확인합니다.

## 6. 현재 시나리오와 실제 통합 사이의 차이

| 차이 | 영향 | 토의·보완 항목 |
| --- | --- | --- |
| 현재 배포 명세가 `container_image`를 필수로 검사 | VM-only 시험 조건과 맞지 않음 | `artifact_uri`, `checksum`, `launch_command` 중심의 VM 실행 명세로 변경 검토 |
| Service-control이 CB-Tumblebug API를 직접 호출하지 않음 | VM 생성 단계가 수동으로 분리됨 | 요청/응답 API와 담당 모듈 결정 |
| SSH 파일 전송·프로세스 실행 adapter가 없음 | AI 응용 실행이 수동 절차에 머묾 | 배포 프로토타입과 실행 계약 정의 |
| VM 상태와 service-control 피드백 API가 없음 | 상태 확인 후 후속 판단이 자동 연결되지 않음 | 공통 상태 schema와 callback/polling 방식 결정 |
| 실제 통합 시험용 AI 응용이 확정되지 않음 | 실행 명령과 성공 판정 정의 불가 | 시험 앱, 입력 데이터, 정상 출력 결정 |

## 7. 회의에서 결정할 항목

1. 통합 시험용 AI 응용과 실행 방식: binary, script, model file 중 무엇을 전달할지
2. 응용 실행 명세: `artifact_uri`, checksum, entrypoint, arguments, environment, timeout
3. CB-Tumblebug VM 생성 요청의 호출 주체와 API 경계
4. VM 실행 결과의 공통 상태값: `pending`, `running`, `succeeded`, `failed`
5. 1차년도 허용 제어 Action: observe, start, stop, restart 중 어디까지 포함할지
6. 자동 연계와 시험 운영자 수동 절차를 최종 시연에서 어떻게 구분해 표시할지

## 8. 권장 발표 문장

> 기존 Ops 시나리오는 LLM의 판단 품질을 평가하기 위한 문제 세트이고, 이번 1차년도 동작 시나리오는 AI 응용 등록부터 Go 기반 Action 검증과 CPU/GPU VM 배치 판단, CB-Tumblebug VM 생성, VM 내 응용 실행 및 상태 확인까지 기관별 기능이 이어지는지를 검증하는 통합 절차입니다. 현재 자동화된 판단 구간과 수동 연계 구간을 구분하고, 회의에서 인터페이스 계약을 확정하는 것이 핵심입니다.
