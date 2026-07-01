# Agent Instructions

Before making code changes, read these files in order:

1. README.md
2. agent_md/00_scope_common_contract.md
3. docs/prompts/프레임워크_공유_프롬프트.md
4. docs/ops/로그_에러_가이드.md
5. contracts/openapi/openapi.yaml
6. contracts/schemas/*.json
7. Relevant agent_md file for the task:
   - API: agent_md/02_api_contract_agent.md
   - Go backend: agent_md/03_go_backend_agent.md
   - App registry: agent_md/04_app_registry_validator_agent.md
   - Deployment: agent_md/05_deployment_orchestrator_agent.md
   - Runtime/resource: agent_md/06_runtime_resource_adapter_agent.md
   - External integration: agent_md/07_external_integration_agent.md
   - External interface: agent_md/10_interface_common_contract.md, agent_md/11_openapi_interface_update_agent.md, agent_md/12_interface_examples_contract_test_agent.md, agent_md/13_external_boundary_review_agent.md
   - Test/release: agent_md/08_test_validation_agent.md, agent_md/09_review_docs_release_agent.md

Hard rules:
- Use Go + Echo.
- All APIs use /api/v1.
- OpenAPI is the source of truth.
- Do not implement Docker, Docker Compose, Kubernetes, OCI image, or Container Registry features for year 1.
- Use artifact.type only as package, git, binary, or script.
- Preserve standard deployment statuses and error codes from agent_md/00_scope_common_contract.md.
- Keep deliverable docs minimal; prefer OpenAPI, prompts, logs, errors, and evidence over long explanatory documents.
- After completing work, add a concise entry to log.md describing what was done and what changed.
