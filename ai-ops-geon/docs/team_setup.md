# 팀 실행 요약

## 로컬

```bash
make test
make vet

cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/team-validation
```

## VM

```bash
cd ~/ai-ops/go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-vm
```

핵심 결과는 실제 VM snapshot 적합성, 등록 에이전트 Action 검증과 비실행 handoff 계획입니다. VM 후보를 내부에서 임의 생성하거나 ranking하지 않습니다.

자세한 절차는 `docs/submission/install_and_run_guide.md`를 확인합니다.
