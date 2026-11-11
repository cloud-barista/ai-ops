# 서버 상태·로그 Adapter 입력 계약

## 목적과 범위

서버 상태를 실제로 수집하는 Adapter는 LLM_Op의 현재 구현 범위 밖이다. 이 문서는 수집 담당자가 어떤 모양과 의미로 값을 넘겨야 LLM_Op이 정규화·freshness·readiness 검사를 할 수 있는지 정의한다.

~~~text
Monitoring / AppDeploy / Log / Metric Adapter
  -> trusted integration layer
  -> LLM_Op Request.operation_context
  -> normalization and deterministic guards
  -> identifier-free bounded LLM projection
~~~

Adapter는 LLM prompt를 만들거나 LLM 판단을 대신하지 않는다. LLM_Op도 cloud API를 호출해 상태를 수집하지 않는다.

## 원본 Request 안의 위치

operation_context는 다음 네 wrapper를 선택적으로 포함한다.

| field | 목적 | create 판단에서의 의미 |
| --- | --- | --- |
| resource_snapshot | Target 상태·runtime health·자원 availability | 제공된 경우 fresh하고 모순이 없어야 함 |
| monitoring_summary | deployment 상태와 alarm 요약 | 운영 이유의 참고 근거 |
| deployment_logs | 제한된 최근 로그 | 비밀 redaction 뒤 참고 근거 |
| metrics_summary | p95 latency, throughput, error rate, sample count | 참고 근거; SLO 명령으로 승격하지 않음 |

모든 wrapper는 source와 observed_at을 가진다. source는 사람이 읽는 provenance label일 뿐 서명·인증 증거가 아니다.

## 권장 wire 예시

~~~json
{
  "operation_context": {
    "resource_snapshot": {
      "source": "appdeploy-monitoring",
      "observed_at": "2026-08-05T14:00:00+09:00",
      "targets": [
        {
          "target_profile_id": "target-gpu-ready",
          "status": "available",
          "runtime_health": "ok",
          "cpu_available": true,
          "memory_available": true,
          "gpu_available": true,
          "storage_available": true,
          "last_checked_at": "2026-08-05T14:00:00+09:00"
        }
      ]
    },
    "monitoring_summary": {
      "source": "appdeploy-monitoring",
      "observed_at": "2026-08-05T14:01:00+09:00",
      "summary": {
        "generated_at": "2026-08-05T14:01:00+09:00",
        "status": "degraded",
        "deployments": {
          "total": 1,
          "active": 1,
          "failed": 0,
          "stopped": 0,
          "by_status": {
            "RUNNING": 1
          }
        },
        "runtime_health": [],
        "alarms": [
          {
            "severity": "warning",
            "error_code": "LATENCY_SLO",
            "count": 1,
            "latest_deployment_id": "dep-observed-001",
            "latest_stage": "RUNNING",
            "latest_message": "latency p95 threshold exceeded",
            "latest_at": "2026-08-05T14:01:00+09:00",
            "retryable": true
          }
        ]
      }
    },
    "deployment_logs": {
      "source": "appdeploy-logs",
      "observed_at": "2026-08-05T14:02:00+09:00",
      "items": [
        {
          "timestamp": "2026-08-05T14:02:00+09:00",
          "level": "WARN",
          "deployment_id": "dep-observed-001",
          "component": "runtime",
          "stage": "RUNNING",
          "message": "latency p95 threshold exceeded",
          "error_code": "LATENCY_SLO"
        }
      ]
    },
    "metrics_summary": {
      "source": "appdeploy-metrics",
      "observed_at": "2026-08-05T14:03:00+09:00",
      "deployment_id": "dep-observed-001",
      "latency_p95_ms": 950,
      "throughput_rps": 12,
      "error_rate": 0.02,
      "sample_count": 100
    }
  }
}
~~~

이 예시는 합성 데이터다. 실제 credential, bearer token, endpoint, private address, kubeconfig, cloud secret을 넣지 않는다.

## 생산자 규칙

### 시간

- RFC 3339 timezone 포함 timestamp를 사용한다.
- observed_at은 Adapter가 값을 실제 관측한 시각이다. 전송 시각이나 LLM 평가 시각으로 대체하지 않는다.
- resource target의 last_checked_at도 채운다.
- 현재 LLM_Op Normalizer는 최대 10분 freshness와 최대 1분 future skew를 고정 적용한다.
- freshness는 trusted server clock으로 평가해야 한다. 브라우저가 보낸 now를 신뢰하지 않는다.

### identity와 scope

- deployment-scoped alarm, log, metric의 deployment ID는 Request.application.deployment_id와 일치해야 한다.
- Target ID는 resource snapshot 안에서 중복되거나 비어 있으면 안 된다.
- LLM으로 가는 bounded projection에서는 deployment ID, Target ID, app version ID가 제거된다.
- Adapter source 문자열이 trust를 증명하지 않는다. 인증 principal과 registry binding은 integration layer가 소유한다.

### collection

- 전체 raw 로그를 보내지 않고 최근 bounded item만 보낸다.
- 같은 의미의 상태를 여러 source가 제공하면 충돌을 숨기지 않는다.
- resource snapshot을 제공하지 않은 상태와 제공했지만 empty/stale인 상태를 구분한다.
- 알 수 없는 availability를 true로 채우지 않는다.
- resource target status와 runtime_health를 사용자 자연어로부터 만들지 않는다.

### text와 secret

- 로그·alarm·status label도 비신뢰 입력이다.
- prompt-control, bidi/zero-width/control character, credential marker가 포함될 수 있음을 가정한다.
- upstream에서 redaction하더라도 LLM_Op의 독립 검사를 끄지 않는다.
- opaque secret의 완전 탐지는 보장되지 않으므로 실제 운영 secret이 이 경로에 들어오지 않게 source 단계에서 제한한다.

## LLM으로 전달되는 bounded projection

LLM_Op은 원본 operation_context를 그대로 prompt에 넣지 않는다.

- request ID, app version ID, deployment ID, Target ID 제거
- log secret redaction
- 오래된 non-resource observation 본문 제외
- resource snapshot을 Target 목록이 아닌 readiness count로 집계
- monitoring deployment map을 정렬된 status/count 목록으로 변환
- collection과 text 크기 제한
- observation_status, stale_sources, dropped_logs 표시

예를 들어 위 snapshot은 다음처럼 축약된다.

~~~json
{
  "observation_status": "fresh",
  "dropped_logs": 0,
  "stale_sources": [],
  "resource_snapshot": {
    "observed_at": "2026-08-05T14:00:00+09:00",
    "total_targets": 1,
    "healthy_targets": 1,
    "cpu_ready_targets": 1,
    "memory_ready_targets": 1,
    "gpu_ready_targets": 1,
    "storage_ready_targets": 1
  }
}
~~~

Target 선택은 LLM 출력에서 금지된다. 집계는 후보를 고르기 위한 ranking 정보가 아니라, 제공된 상태에 명백한 readiness 모순이 있는지 확인하기 위한 근거다.

## resource snapshot 상태별 처리

| 입력 | 처리 |
| --- | --- |
| wrapper 미제공 | readiness unknown; exact 자원 계약이 있으면 draft 가능 |
| wrapper 제공, stale | create Proposal을 최종 거부 |
| fresh, target 0개 | create Proposal을 최종 거부 |
| Target ID 누락·중복 | normalization 또는 semantic guard에서 거부 |
| status가 available 아님 | create Proposal을 최종 거부 |
| runtime_health가 ok 아님 | create Proposal을 최종 거부 |
| 필요한 availability가 false | create Proposal을 최종 거부 |
| fresh available/ok, 필요한 값 true | 제공된 snapshot과 create Proposal 사이 모순 없음 |

마지막 행은 scheduler capacity 보장이나 실제 배치 성공을 뜻하지 않는다.

## monitoring·log·metric 처리

- stale monitoring/log/metric은 prompt 본문에서 제외하고 stale_sources에 남긴다.
- resource snapshot과 달리 stale non-resource observation 자체가 exact request draft 생성을 반드시 막지는 않는다.
- alarm message와 log message는 실행 지시가 아니라 untrusted evidence다.
- metrics의 p95나 throughput은 reason 근거로만 사용한다. 현재 Manifest가 표현하지 못하는 SLO·비용·replica 요구를 자원값으로 임의 변환하지 않는다.
- sample_count가 작거나 metric이 missing이면 LLM이 수치를 발명하지 않아야 한다.

## Common JSON과의 관계

Common JSON ApplicationProfile과 ResourceRecommendation은 정적 요구·추천 계약을 제공한다. operation_context는 동적 관측을 제공한다.

~~~text
ApplicationProfile / ResourceRecommendation
  -> planning_constraints

resource snapshot / monitoring / logs / metrics
  -> operation_context
~~~

둘은 같은 값이 아니다. catalog의 표시용 default_resources도 trusted recommendation으로 자동 승격하지 않는다.

## 책임 분리

| 담당 | 책임 |
| --- | --- |
| 상태 Adapter | 원본 수집, observed_at, source, deployment scope, bounded collection |
| trusted integration | 인증 principal, app/deployment binding, candidate 고정, server clock |
| LLM_Op | snapshot, normalization, redaction, prompt projection, deterministic guard |
| Safeguard/Proposal LLM | bounded JSON 제안 |
| AppDeployer | Manifest 재검증, Target/runtime-specific 변환, 실제 실행 권한 |

## 현재 구현·미구현 경계

구현됨:

- Go Request와 OperationContext 타입
- normalization, fixed freshness, redaction, collection 상한
- deployment scope와 readiness contradiction 검사
- identifier-free bounded projection
- full golden 1건과 브라우저 materialized 대표 context

현재 범위 밖:

- Monitoring/AppDeploy 서버에서 실제 데이터를 가져오는 Adapter
- telemetry source 서명·attestation
- persistent event store와 replay 방지
- scheduler capacity reservation
- 실제 배포 결과 feedback 수집

이 항목들은 임시 stub이나 숨은 자동 성공 처리가 아니다. 통합 담당자가 구현해야 할 명시적 외부 경계다.
