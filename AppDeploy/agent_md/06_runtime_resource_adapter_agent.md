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

## CPU VM 기준
- Ubuntu VM에서 패키지/스크립트/바이너리 실행을 지원한다.
- SSH 방식 또는 VM-side Agent 방식 중 환경에 맞게 선택한다.
- credential_ref만 사용하고 Secret 값을 코드/로그에 쓰지 않는다.

## GPU VM 기준
- nvidia-smi 실행 가능 여부를 확인한다.
- GPU 개수와 App 요구량을 비교한다.
- Driver/CUDA 정보는 로그로 남긴다.
- GPU 미탐지 시 `GPU_RUNTIME_NOT_FOUND` 또는 `NVIDIA_DRIVER_NOT_FOUND`를 반환한다.

## 컨테이너 금지
- Docker, Kubernetes, container image pull, registry login 로직을 구현하지 않는다.
