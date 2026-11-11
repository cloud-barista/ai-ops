# AI-App 자연어 변경 승인

자연어 에이전트는 OpsLLM이 선택한 작업과 지시에 포함된 배포 ID를 Run에 기록한다. 운영자는 배포 구성이나 제거 유형을 지정해 계획을 검토한 뒤 같은 계획의 해시를 승인한다. 새 배포와 업데이트는 AI-App의 기존 승인 Revision 적용 도구를 호출한다. [기존 Revision API](../../../ai-app/docs/api/approved-deployment.md)

## 호출 주체와 권한

`AIOPS_FRAMEWORK_ACCESS_POLICY_FILE`에 JSON 정책 경로를 설정한다. 각 호출 주체의 토큰은 지정한 `AIOPS_PRINCIPAL_*_TOKEN` 환경변수에 둔다. 호출 주체마다 다른 값을 사용하고 AI-App 게이트웨이·엔진 승인·등록·OpsLLM·관리자 토큰과 구분한다. 에이전트, 대상 프레임워크, 네임스페이스와 작업이 모두 일치해야 한다.

같은 정책 파일에 AI-Infra·AI-HA·AI-Conn의 조회용 MCP 에이전트와 권한을 추가할 수 있다. [프레임워크 MCP 등록](framework-mcp-registry.md)

```json
{
  "version": 1,
  "principals": [
    {
      "name": "release-operator",
      "token_env": "AIOPS_PRINCIPAL_RELEASE_OPERATOR_TOKEN",
      "grants": [{
        "agent": "ai-mcmp-app-automation-engine",
        "framework": "AI-App",
        "namespace": "etri",
        "actions": [
          "interpret", "list_applications", "application_status",
          "deploy_application", "update_application", "heal_application",
          "control_application.suspend", "control_application.resume", "remove_application"
        ]
      }]
    }
  ]
}
```

정책은 시작할 때 읽고 `runs/framework-access/history/`에 활성화 시각과 해시를 남긴다. `interpret`는 자연어 요청과 본인 Run 읽기를 허용한다. 카탈로그·배포 상태 조회에는 각 조회 작업의 권한이 추가로 필요하다. 제한 계정은 AI-Ops 주소에 직접 연결한다. 기존 8090 프록시의 게이트웨이 토큰은 종전 자연어 계약에서 사용한다.

## REST 승인

1. 기존 `POST /aiapp/partner/nl/ns/{nsId}/intents`에서 쓰기 제안의 `runId`를 받는다. 배포를 변경하거나 제어할 때는 지시에 정확한 배포 ID 하나를 넣는다.
2. 허용된 호출 주체의 bearer로 `POST /api/v1/integrations/ai-app/intent-changes/{run_id}/prepare`를 호출한다. 아래 작업별 입력을 넣고 응답의 대상·도구·구성과 `digest`를 확인한다.
3. 같은 경로의 `/approve`에 `{"digest":"<reviewed-digest>"}`를 보낸다. `GET /api/v1/integrations/ai-app/intent-changes/{run_id}`에서 적용·제거 상태와 Run·배포 ID를 확인한다.

| 작업 | `prepare` 입력과 확인 조건 |
| --- | --- |
| 새 배포 | `correlation_id`, `apply`를 넣는다. `apply`는 기존 적용 요청과 같은 형식이며 Guard가 승인하고 dry-run을 마친 초기 Revision과 일치해야 한다. |
| 응용 버전 변경 | `correlation_id`, 다음 Revision의 `apply`를 넣는다. 지시의 배포 ID는 현재 활성 응용의 ID여야 한다. 추론 설정과 배치, 응용의 연결 설정은 이전 승인과 일치해야 한다. |
| 실패한 승인 배포 재시도 | `correlation_id`, 다음 Revision의 `apply`를 넣는다. 실패한 이전 Revision의 배포 ID와 구성을 사용한다. 일반 실패 배포는 빈 객체로 기존 복구 도구를 호출한다. |
| VM 정지·재개·재부팅·종료 | 빈 객체를 보낸다. 각 동작에 별도 권한이 필요하다. VM 종료에는 `control_application.terminate`를 부여하고 `approve` 본문에 `"confirm_termination": true`를 넣는다. |
| 응용·추론 서비스 제거 | `{"deployment_type":"app"}` 또는 `{"deployment_type":"inference"}`를 보낸다. 기존 AI-App의 `delete_deployment` MCP가 배포 제거를 실행한다. |

계획과 승인 기록은 `runs/aiapp-intent-changes/{run_id}/`에 저장된다. 정책·엔진 식별자·대상이 달라지면 다시 검토한다. AI-App이 승인된 Revision을 `confirmation_pending`으로 확인하면 원래 승인자가 같은 해시로 `/approve`를 호출해 이어갈 수 있다. 이때 AI-Ops가 저장한 동일 본문을 보내고 AI-App은 예약한 제출 ID를 재사용한다. 적용 영수증을 확인할 수 없으면 기존 승인 상태를 반환한다. 개별 제거·일반 복구·VM 제어의 확인 대기는 배포 상태, 엔진 Run과 CB-Tumblebug 노드 상태를 대조한다.

## MCP 승인

정책을 설정하면 AI-Ops의 `/aiapp/partner/nl/mcp`에 아래 도구가 추가된다. REST와 같은 계획·승인 기록을 사용한다. 호출 주체의 bearer와 `X-AIMCMP-Namespace`를 보낸다.

| 도구 | 인수 |
| --- | --- |
| `prepare_application_intent_change` | `runId`, `request`에 REST 준비 본문 |
| `approve_application_intent_change` | `runId`, `request`에 REST 승인 본문 |
| `get_application_intent_change` | `runId` |

## 추론 최적화 제안 승인

첫 vLLM 배포에서 최적화 시험의 기준값을 정하려면 `/api/v1/agent-control/flows/{correlation_id}/aiapp/prepare`에 `model_id`, `artifact_id`와 양의 정수 `max_num_seqs`를 보낸다. 예를 들어 `"max_num_seqs": 16`은 준비한 추론 요청의 `inference.args`에 `["--max-num-seqs", "16"]`으로 들어간다. 운영자가 이 추론 요청으로 `/aiapp/stage`를 승인하고 같은 추론 본문을 Revision 1의 `/aiapp/apply`에 넣는다. 첫 적용의 관측에서 `maxNumSeqs`를 읽을 수 있다. [AI-App 관측 API](../../../ai-app/docs/api/inference-observation.md)

AI-App 학습 작업이 게시한 제안은 `/api/v1/agent-control/flows/{correlation_id}/aiapp/optimizations/{proposal_id}`에서 검토한다. 같은 정책 파일의 AI-App 엔진·네임스페이스 권한에 `optimize_inference.read`, `optimize_inference.approve`, `optimize_inference.compare` 중 필요한 작업을 추가한다. [AI-App 게시와 비교](../../../ai-app/docs/api/inference-optimization.md)

| 요청 | 사용 |
| --- | --- |
| `GET` | AI-App 승인 MCP에서 제안을 읽는다. 현재 근거가 유효하면 적용할 정확한 `nextRevision`을 보여 준다. 승인 후에는 저장된 요청과 적용 상태를 읽는다. |
| `POST /approve` | `{"digest":"<proposal_id>"}`로 검토한 제안을 승인한다. 처음 승인할 때 AI-App에서 현재 Revision과 배포 ID, 과거 응용 전환 계측을 확인한다. AI-App이 저장된 적용을 `confirmation_pending`으로 확인하면 원래 승인자가 같은 해시로 이어갈 수 있다. |
| `POST /compare` | 같은 부하의 새 `load`와 이번 적용의 `transitionEvidence`를 보낸다. AI-App은 두 응용 배포 ID와 응답 시각을 대조해 중단 시간을 계산한다. 같은 관측 창의 결과를 한 번만 저장하고 기준 미달 시 `rollbackDraft`를 반환한다. |

승인과 사후 비교는 `runs/aiapp-optimizations/{correlation_id}/{proposal_id}/`에 보관한다. 제안 조회와 비교는 AI-App의 읽기 MCP를 사용한다. 복구 초안의 적용에는 기존 운영자 승인이 필요하다.

과거 전환 근거 없이 게시된 이전 제안을 비교할 때는 `transitionEvidence` 대신 `downtimeSeconds`를 보낼 수 있다. 이번 적용의 응답 시각을 보낼 때는 두 필드 중 `transitionEvidence`만 사용한다. [전환 근거 형식](../../../ai-app/docs/api/inference-optimization.md#적용-후-비교)
