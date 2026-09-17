# Legacy all-in-one prototype

The focused deployment-agent process does not construct or register the former embedded requirement-analysis, mock resource-recommendation, LLM_Op safeguard, AppDeploy planner, autonomy, or web UI paths.

Legacy wiring is isolated by:

- `api.NewLegacyService`
- `api.registerLegacyRoutes`
- `AIOPS_LEGACY_API_ENABLED=true`

The underlying packages remain in place for reproducibility and existing tests. New focused code must not import them into `NewFocusedService` or expose their routes through `registerFocusedRoutes`.
