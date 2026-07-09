# 06. Runtime/Resource Adapter 에이전트

## 역할
Mock Runtime, CPU VM Runtime, GPU VM Runtime, Resource Matcher를 구현한다.

## Runtime Adapter Interface
- ValidateTarget
- HealthCheck
- Prepare
- Deploy
- GetStatus
- GetLogs
- Stop

모든 Runtime Adapter 메서드는 `context.Context`를 첫 번째 인자로 받고, SSH/HTTP/file I/O/외부 API 호출에 timeout과 cancellation을 적용한다.

## CPU VM 기준
- Ubuntu VM에서 패키지/스크립트/바이너리 실행을 지원한다.
- SSH 방식 또는 VM-side Agent 방식 중 환경에 맞게 선택한다.
- credential_ref만 사용하고 Secret 값을 코드/로그에 쓰지 않는다.
- SSH command, upload, process stop 결과는 `zerolog` 구조화 로그와 DeploymentEvent에 남기되 host, key path, password, token 원문은 마스킹한다.
- 장시간 실행 command는 context cancellation으로 종료 가능해야 한다.

## GPU VM 기준
- nvidia-smi 실행 가능 여부를 확인한다.
- GPU 개수와 App 요구량을 비교한다.
- Driver/CUDA 정보는 로그로 남긴다.
- GPU 미탐지 시 `GPU_RUNTIME_NOT_FOUND` 또는 `NVIDIA_DRIVER_NOT_FOUND`를 반환한다.
- GPU readiness, driver/CUDA, deploy/stop 로그에는 `request_id`, `deployment_id`, `target_profile_id`, `component=gpu-vm-adapter`, `stage`를 가능한 한 포함한다.

## 동시성 및 외부 호출 기준
- goroutine fan-out은 `sync.WaitGroup` 또는 `errgroup`으로 관리한다.
- 공유 상태는 `sync.Mutex` 또는 `sync.RWMutex`로 보호한다.
- 모든 goroutine은 context cancellation 또는 channel close로 종료 경로를 가진다.
- 외부 HTTP/API/SSH 호출은 timeout을 가진 client 또는 context deadline을 사용한다.
- retry는 idempotent readiness/status 조회 같은 작업에만 적용하고, exponential backoff와 jitter를 사용한다.
- error는 무시하지 않고 문맥을 감싸 반환한다. Adapter 내부 raw error를 API 응답에 직접 노출하지 않는다.

## 컨테이너 금지
- Docker, Kubernetes, container image pull, registry login 로직을 구현하지 않는다.
