# LLM_Op

자연어 또는 구조화된 응용 요구사항을 분석해 `ApplicationProfile`을 생성합니다.

## 핵심 기능

- CPU, 메모리, GPU, 저장소 및 서비스 조건의 구조화
- Qwen, Gemma, Llama 등 분석용 LLM 후보의 비교와 선택
- 입력·출력 계약 검증과 분석 실패 구분

인프라 추천은 `resource_ops`, 배포 및 운영 판단은 `deployment_agent`가 담당합니다.

## 실행

Go 1.25 이상이 필요합니다. 모델 호출을 사용하려면 후보 설정에 맞는 LLM endpoint를 준비하세요.

```bash
cd llmop/go/service-control-api
go run ./cmd/llmop-api
```

## 검증

```bash
go test ./...
go vet ./...
```

후보 설정: [`config/`](config/) / 상세 가이드: [docs/usage.md](docs/usage.md)
