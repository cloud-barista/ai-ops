# AI-Ops Go Prototype Experiment Summary

## Experiment scope

This experiment validates the Kyung Hee University service-control prototype for the following research deliverables:

1. LLM 운영 관리 구조 설계서
2. 에이전트 등록 관리 프로토타입
3. AI 응용 배포·제어 추론 최적화 전략 설계서

## Local Go validation

- Branch: geon
- Go implementation: service-control-api and aiops-guard
- Unit tests:
  - go/aiops-guard: passed
  - go/service-control-api: passed
- Team validation command: passed
- Team validation summary: team-validation/00_team_validation_summary.json

Validated pipeline steps:

- Ops LLM selection
- Agent registry listing
- Agent action validation
- CPU/GPU VM inference placement recommendation
- AI application deployment/control planning
- Service operation readiness execution

## API validation

- API health endpoint: passed
- Service operation endpoint: passed
- Selected LLM: primary-ops-llm
- Selected resource: gpu-vm-l4
- Guard backend: go
- Endpoint result: 10_api_service_operations_run.json

## CB-Tumblebug and AWS GPU VM validation

- Infra ID: mc-gpu-small-test
- Node ID: nvidial4small-1
- Region: us-west-2
- Instance type: g6.xlarge
- GPU: NVIDIA L4
- Public IP: 34.212.5.116
- CB-Tumblebug status: Running:1 (R:1/1)
- CB-Tumblebug status evidence: 05_cb_tumblebug_infra_status.json
- Remote GPU evidence: 06_remote_nvidia_smi.txt

The remote SSH validation confirmed NVIDIA-SMI 595.71.05, CUDA 13.2, and NVIDIA L4 GPU visibility on the AWS VM.

## Important note

The Go service-control prototype validates LLM selection, agent registry, placement, and deployment/control planning locally. Actual AWS GPU VM provisioning and GPU device verification were validated separately through CB-Tumblebug. Kubernetes apply/recovery execution remains mock-mode in this project experiment unless a target Kubernetes runtime is connected.

## Cost note

The AWS GPU VM is still a live billable resource while it remains running. Delete the CB-Tumblebug infra after finishing validation.