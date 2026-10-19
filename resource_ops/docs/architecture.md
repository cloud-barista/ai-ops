# Resource Ops Architecture

## 역할 경계

Resource Ops는 `ApplicationProfile → internal ResourceQuery → ResourceRecommendation`을 소유한다. ApplicationProfile 생성은 LLM_Op, deployment/scaling/feedback/manifest는 downstream 에이전트가 소유한다.

## 처리 흐름

```text
HTTP ApplicationProfile
  → deterministic ResourceQuery projection
  → recommendation query validation
  → resource.Source.Collect
  → source adapter RawFact[]
  → resource.Normalize
  → ResourceProfile[]
  → mandatory availability filtering
  → static snapshot scoring
  → deterministic ranking
  → ResourceRecommendation
```

## 변경 격리

외부 전달 형식은 `resource.Source` 뒤에 숨긴다. 현재 Prometheus 구현은 metric별 vector 응답을 source-neutral `RawFact`로 투영한다. 이후 API, message queue, inventory DB 또는 Common JSON으로 입력이 바뀌면 새로운 Source만 구현한다.

추천 서비스가 의존하는 값은 다음뿐이다.

- resource와 device identity
- 관측 시각과 health
- CPU 총량·가용량·사용률
- 메모리 총량·가용량
- 스토리지 총량·가용량
- accelerator 유형·모델·장치 수·총/가용 메모리·사용률
- 선택적 provider, region, architecture, resource type, labels

## Prometheus 임시 어댑터

모든 instant query는 하나의 평가 시각을 공유한다. node exporter와 DCGM exporter는 `node_id`로 결합한다. GPU는 `UUID`, 그 값이 없으면 `gpu` 또는 `device` label을 장치 ID로 사용한다.

필수 node metric query가 실패하거나 비어 있으면 Source unavailable로 처리한다. GPU 및 부가 metric query 실패는 warning으로 보존되며, GPU가 필수인 요청은 가속기 availability 필터에서 fail-closed 된다.

## 결정성

동일 Snapshot과 Query에 대해:

- 리소스와 제외 목록은 `resource_id` 순으로 안정화한다.
- 후보는 2자리 score 내림차순으로 정렬한다.
- 동점은 `resource_id` 오름차순으로 해소한다.
- 출력 rank는 1부터 시작한다.

## 비책임 영역

- LLM 분석과 ApplicationProfile 생성
- VM 생성·삭제
- 배포 대상 확정과 Manifest 생성
- current deployment 상태 판단
- scale in/out 및 repair
- scheduler handoff

추천은 advisory이므로 실제 배포 직전에 downstream 계층이 용량과 동시성 상태를 재검증해야 한다.
