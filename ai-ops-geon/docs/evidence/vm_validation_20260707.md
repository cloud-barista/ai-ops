# AWS GPU VM 검증 결과

본 문서는 `geon` 브랜치의 Go 기반 service-control prototype을 AWS GPU VM 내부에서 실행한 검증 결과입니다.

## 검증 환경

| 항목 | 값 |
| --- | --- |
| 실행 일자 | 2026-07-07 |
| 실행 위치 | AWS GPU VM |
| Instance type | `g6.xlarge` |
| Region | `us-west-2` |
| GPU | NVIDIA L4 |
| OS | Ubuntu 22.04 |
| Go version | `go1.25.4 linux/amd64` |
| Driver/CUDA | NVIDIA Driver 595.71.05, CUDA 13.2 |

## 실행 명령

```bash
cd ~/ai-ops/go/service-control-api

go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --output-dir ../../runs/full-validation-vm-gpu
```

## 결과 요약

| 검증 단계 | 결과 |
| --- | --- |
| Go module test: `aiops-guard` | valid |
| Go module test: `service-control-api` | valid |
| Team validation | valid |
| GPU visibility: `nvidia-smi` | valid |
| AWS instance metadata | valid |
| 전체 `validate-system --target vm` | valid |

`ops-llm-evaluation`과 `api-integration-validation`은 이번 명령에서 optional flag를 켜지 않았기 때문에 `skipped=true`로 기록되었습니다. 이는 실패가 아니라 이번 VM 검증 범위 밖임을 의미합니다.

## 대표 증적 파일

| 파일 | 설명 |
| --- | --- |
| [`artifacts/vm_20260707_system_validation_summary.json`](artifacts/vm_20260707_system_validation_summary.json) | VM `validate-system` summary |
| [`artifacts/vm_20260707_team_validation_summary.json`](artifacts/vm_20260707_team_validation_summary.json) | VM 내부 team-validation summary |
| [`artifacts/vm_20260707_nvidia_smi.txt`](artifacts/vm_20260707_nvidia_smi.txt) | NVIDIA L4 visibility evidence |
| [`artifacts/vm_20260707_aws_metadata.json`](artifacts/vm_20260707_aws_metadata.json) | AWS VM metadata evidence |

## 해석 경계

이번 검증으로 확인한 것은 AWS GPU VM 환경에서 Go 기반 service-control prototype이 정상 실행되고, NVIDIA L4 GPU와 AWS metadata를 인식했다는 점입니다.

다음 항목은 이번 검증에서 수행하지 않았습니다.

- 실제 LLM benchmark 실행
- 실제 운영 서비스 배포 완료
