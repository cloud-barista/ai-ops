# Resource Ops

`ApplicationProfile`과 자원 관측을 사용해 실행 가능한 인프라 후보를 추천합니다.

## 핵심 기능

- 소스별 자원 정보를 공통 `ResourceSnapshot`으로 정규화
- 요구 자원과 관측 유효성 검사
- 조건을 만족하는 후보의 점수화와 `ResourceRecommendation` 생성

요구사항 분석과 배포·복제본 조절 판단은 수행하지 않습니다.

## 실행

Go 1.26.5가 필요합니다. `RESOURCE_PROMETHEUS_URL`로 사용할 관측 endpoint를 지정하세요.

```bash
cd resource_ops
go run ./cmd/server
```

## 검증

```bash
go test ./...
go vet ./...
```

입출력 스키마: [`schemas/`](schemas/) / 상세 가이드: [docs/usage.md](docs/usage.md)
