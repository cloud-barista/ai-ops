# 실제 CPU/GPU VM 적합성 검증 가이드

## 목적

이 가이드는 인프라 계층이 제공한 실제 VM snapshot을 AI workload 요구사항과 비교하는 방법을 설명합니다. 가상 후보 ranking이나 예상 성능 score는 사용하지 않습니다.

## 입력과 상태

| 입력 | 파일 |
| --- | --- |
| Workload 요구사항 | `config/vm_workload_requirements.json` |
| 기록된 VM 예시 | `docs/evidence/artifacts/vm_20260707_resource_snapshot.json` |
| 라이브 VM 결과 | `runs/<validation>/06_vm_resource_snapshot.json` |

판단 상태는 `incompatible`, `provisionally_compatible`, `compatible` 세 가지입니다. 성능이 측정되지 않았으면 자원 조건이 통과해도 잠정 적합으로 기록합니다. 성능이 측정된 경우에는 선언된 latency SLO 이하인지, 최소 throughput 이상인지 실제값으로 비교하며 기준을 벗어나면 `incompatible`로 판정합니다.

## 검증 항목

1. VM snapshot의 출처와 수집 상태
2. workload의 accelerator 요구조건
3. 선언된 최소 CPU·메모리·VRAM 조건
4. 실제 benchmark가 제공한 latency·throughput·cost 상태

## CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

이 명령은 VM을 선택하거나 생성하지 않습니다. 전달된 VM 하나가 해당 workload에 적합한지만 검증합니다.
