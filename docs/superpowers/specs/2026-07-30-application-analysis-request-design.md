# Application Analysis Request Compatibility Design

## Goal

Accept the Common JSON v1.0 `application.analysis.request` message defined by
the KHU communication protocol and execute the existing one-shot geon
automation pipeline without duplicating the analyzer, recommender, Agent
Registry, decision, or Guard logic.

## API

`POST /api/v1/agent-control/application-analysis-requests`

The request uses the protocol Common Envelope and carries:

- application identity and artifact
- natural-language `user_request`
- declared workload hints
- labels

The first valid request returns `201 Created` with an `AutomationRun`. Repeating
the same `message_id` returns `200 OK`, the same `run_id`, and the response
header `Idempotent-Replayed: true`.

## Processing

1. Validate Common Envelope version, message type, identifiers, endpoints, and
   application payload.
2. Normalize the protocol payload into `AutomationRunInput`.
3. Preserve the incoming `correlation_id` and `trace_id`.
4. Run the existing Requirement Analyzer, Resource Recommender, Agent Registry
   authorization, deployment decision, and Go Guard pipeline.
5. Set the generated `application.context.created.causation_id` to the incoming
   request `message_id`.
6. Cache the completed or failed run by incoming `message_id`.

The existing `/automation-runs` endpoint remains compatible and continues to
generate its own flow identifiers.

## Idempotency Boundary

geon prevents duplicate analysis and duplicate `deployment.create.request`
generation for the same incoming `message_id`. The generated deployment
`request_id` therefore remains stable on replay. The downstream deployment
orchestrator remains responsible for enforcing `request_id` idempotency during
actual deployment execution.

## Verification

- model and mapping unit tests
- first-request and replay API tests
- malformed-envelope rejection tests
- OpenAPI contract checks
- full Go test, vet, and build

