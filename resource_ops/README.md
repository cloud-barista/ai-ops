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

API 문서는 플랫폼의 [통합 Swagger UI](http://127.0.0.1:18100/swagger)에서 확인하고 요청을 실행할 수 있습니다. 이 서비스의 원본 명세는 `/openapi.yaml`에서 내려받습니다.

`ApplicationProfile` 요청과 추천 응답, 오류 코드 및 legacy `ResourceQuery` 입력을 문서화합니다. Go 주석을 변경한 뒤 이 폴더에서 `make swag`를 실행하면 [`docs/swagger/`](docs/swagger/)의 JSON/YAML을 갱신합니다.

## 검증

```bash
go test ./...
go vet ./...
```

입출력 스키마: [`schemas/`](schemas/) / 상세 가이드: [docs/usage.md](docs/usage.md)
