# 프레임워크 MCP 등록과 조회

AI-Ops는 운영자가 설정한 에이전트에 Streamable HTTP MCP로 접속한다. 프레임워크와 네임스페이스는 등록 설정에 고정한다. 호출자는 자신의 bearer로 허용된 작업 이름과 식별자만 전달한다. Innogrid AI-App 카드 등록은 기존 `/api/v1/integrations/ai-app/agents`를 사용한다.

## 설정

`AIOPS_FRAMEWORK_ACCESS_POLICY_FILE`로 지정한 정책에 `mcp_peers`를 추가한다. 아래 예시는 `khu-test`의 CB-Tumblebug 인프라 목록과 상세 조회를 연결한다. 기존 AI-App과 AI-Ops 권한은 같은 파일의 `principals`에 함께 둘 수 있다.

```json
{
  "version": 1,
  "principals": [
    {
      "name": "infra-registry-manager",
      "token_env": "AIOPS_PRINCIPAL_INFRA_MANAGER_TOKEN",
      "grants": [
        {
          "agent": "cb-infra",
          "framework": "AI-Infra",
          "namespace": "khu-test",
          "actions": ["registry.manage", "registry.read"]
        }
      ]
    },
    {
      "name": "infra-inventory-reader",
      "token_env": "AIOPS_PRINCIPAL_INFRA_READER_TOKEN",
      "grants": [
        {
          "agent": "cb-infra",
          "framework": "AI-Infra",
          "namespace": "khu-test",
          "actions": ["read.infras", "read.infra"]
        }
      ]
    }
  ],
  "mcp_peers": {
    "cb-infra": {
      "framework": "AI-Infra",
      "namespace": "khu-test",
      "role": "CB-Tumblebug infrastructure inventory",
      "endpoint": "https://infra-mcp.example.org/mcp",
      "server_name": "cb-tumblebug",
      "token_env": "AIOPS_MCP_CB_INFRA_TOKEN",
      "read_tools": {
        "read.infras": {"name": "get_infra_list", "namespace_argument": "ns_id"},
        "read.infra": {"name": "get_infra", "namespace_argument": "ns_id", "arguments": ["infra_id"]}
      }
    }
  }
}
```

- `endpoint`에는 실제 MCP 경로까지 포함한다. 원격 연결은 HTTPS를 사용하고, 로컬 시험에는 루프백 IP의 HTTP를 쓸 수 있다. CB-Tumblebug 앞단에서는 전용 bearer를 검증한다. CB-Tumblebug가 자체 REST API에 쓰는 자격 증명은 서버 설정으로 관리한다. [CB-Tumblebug MCP 설정](https://github.com/cloud-barista/cb-tumblebug/tree/b808b9a2/src/interface/mcp)
- 각 호출 주체와 MCP 접속에는 서로 다른 토큰을 설정한다. 파일에는 환경변수 이름을 적는다. 동일 프레임워크의 네임스페이스를 추가하려면 각각의 에이전트 이름·정책·접속 자격 증명을 설정한다.
- 각 조회 도구는 대상 범위를 결정하는 문자열 `namespace_argument`를 제공해야 한다. 운영자가 해당 인수가 도구에서 실제로 적용되는지 확인한 뒤 이름을 등록한다. `arguments`에 지정한 문자열 식별자만 호출자가 전달한다. 서버의 읽기 선언을 대조하며, 원본 CB-Tumblebug의 `get_infra_list(ns_id)`와 `get_infra(ns_id, infra_id)`에는 소스에서 확인한 GET 계약을 적용한다. [CB-Tumblebug 조회 도구](https://github.com/cloud-barista/cb-tumblebug/blob/b808b9a2/src/interface/mcp/tb-mcp.py#L3393)

## 등록과 호출

```sh
agent_url=http://127.0.0.1:18130/api/v1/framework-agents/cb-infra

curl --fail-with-body -X PUT "$agent_url" \
  -H "Authorization: Bearer $AIOPS_PRINCIPAL_INFRA_MANAGER_TOKEN"

curl --fail-with-body "$agent_url" \
  -H "Authorization: Bearer $AIOPS_PRINCIPAL_INFRA_MANAGER_TOKEN"

curl --fail-with-body "$agent_url/actions/read.infras/invoke" \
  -H "Authorization: Bearer $AIOPS_PRINCIPAL_INFRA_READER_TOKEN" \
  -H 'Content-Type: application/json' -d '{}'

curl --fail-with-body "$agent_url/actions/read.infra/invoke" \
  -H "Authorization: Bearer $AIOPS_PRINCIPAL_INFRA_READER_TOKEN" \
  -H 'Content-Type: application/json' -d '{"infra_id":"gpu-pool"}'
```

등록은 MCP `initialize`와 `tools/list`를 검증하고 `runs/framework-mcp/<agent>.json`에 저장한다. 같은 계약을 다시 등록하면 기존 등록 시각을 반환한다. 재시작 후 조회는 같은 저장본을 읽는다. 도구 호출 직전 서버 이름·버전과 입력·출력 스키마를 다시 확인한다. 설정이나 선언이 바뀌면 `409`를 반환하므로 계약을 검토한 뒤 다시 등록한다. 등록 삭제는 `registry.manage`로 `DELETE`를 호출한다. 서버 로그에 호출 주체·에이전트·네임스페이스·작업·결과 상태가 남는다. [OpenAPI](openapi_deployment_agent.yaml)

원본 CB-Tumblebug MCP와 로컬 REST 표본으로 등록, AI-Ops 프로세스 재시작, 목록·상세 조회를 확인했다. AI-HA·AI-Conn은 담당 기관의 MCP 주소·인증·네임스페이스·조회 도구 계약이 확정되면 연결한다. 프레임워크 MCP 등록 경로는 조회를 지원하며, AI-App 배포와 제어는 기존 승인 경로에서 실행한다. [등록·권한 시험](../go/service-control-api/internal/api/framework_mcp_test.go) · [AI-App 승인](aiapp-intent-approval.md)
