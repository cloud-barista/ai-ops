# AI App Deployer prototype architecture

The deployer is a headless execution service. The AI automation agent owns
user interaction and policy resolution; the deployer owns validation,
placement, execution, state, and logs.

```text
Agent
  ├── original Application ──> App Service ──> Application Repository
  └── DeploymentRequest ─────> Orchestrator
                                  │
                    ResourceInformationProvider
                                  │
                         PlacementProvider
                                  │
                         PlacementDecision
                                  │
                           Runtime Adapter
                                  │
                       selected local/remote VM
```

## Boundaries

### App Management

`internal/app` validates the App Spec, rejects container artifacts, checks
duplicate name/version pairs, and stores the App Spec plus the exact original
JSON document. Artifact content is not rewritten. A runtime may create a
working directory, but it does not replace the registered artifact.

### Deployment Orchestrator

`internal/deployment` normalizes the deployment request into a manifest,
loads the registered application, merges omitted requirement values from the
App Spec, calls the resource and placement interfaces, persists the decision,
invokes the runtime router, and records events. It does not know local
scheduling rules or an ETRI request schema.

The compatibility event sequence remains:

```text
REQUESTED / PENDING
  -> VALIDATING / PLACING
  -> VALIDATED / SCHEDULING
  -> DEPLOYING
  -> RUNNING or COMPLETED

failure: VALIDATION_FAILED, SCHEDULING_FAILED, DEPLOYMENT_FAILED, or RUNTIME_FAILED
stop: STOPPING -> STOPPED
```

`PENDING` and `PLACING` make the new lifecycle explicit while the existing
`REQUESTED`, `VALIDATING`, `VALIDATED`, and `SCHEDULING` events remain for API
compatibility.

### Local VM Registry and scheduler

The existing Target Profile repository stores local VM registry records. New
fields are `vm_type`, `status`, `capacity`, `allocated`,
`supported_runtimes`, `cost_weight`, and `labels`. `vm.credential_ref` is a
reference only; secrets are resolved from the credential service/environment
and are never returned in logs.

`internal/provider/LocalPlacementProvider` applies these deterministic rules:

1. only READY nodes are eligible (legacy empty status is treated as READY);
2. CPU, memory, GPU count/type, storage, runtime, and labels are checked;
3. `min_cost` chooses the lowest cost weight;
4. equal cost chooses greater remaining capacity, then lexicographically
   smaller VM ID;
5. the repository reserves capacity before returning a decision;
6. failure or stop releases the deployment reservation.

Memory and file repositories both perform reservation updates under their
repository lock. This prevents two concurrent requests from consuming the
same local capacity.

### Runtime adapters

`internal/runtime.Router` remains the runtime selection point. The new
`internal/runtime/local` adapter runs a real local process with `os/exec`,
records the PID-derived runtime ID, captures stdout/stderr, reports
`RUNNING`, `COMPLETED`, `RUNTIME_FAILED`, and `STOPPED`, and supports kill-based
stop. Existing CPU VM, GPU VM, Mock, and ETRI AI-Infrastructure adapters are
unchanged and remain behind the router.

## ETRI replacement points

`ResourceInformationProvider` and `PlacementProvider` are defined in
`internal/provider/interfaces.go`. Replace the following implementations
when the ETRI contract is available:

- `ETRIResourceMetadataAdapter`: DTOs and mapper for resource metadata;
- `ETRIPlacementAdapter`: DTOs and mapper for placement requests/decisions;
- external error mapping: map timeout, authentication, invalid response, and
  provider failures to the existing internal error codes.

ETRI endpoint paths, DTOs, and authentication semantics are intentionally
not guessed. Configure `ETRI_RESOURCE_ENDPOINT`, `ETRI_PLACEMENT_ENDPOINT`,
`ETRI_TIMEOUT`, and the credential reference when the partner contract is
available; a selected ETRI provider with a missing endpoint fails at startup.

The Orchestrator constructor and business flow do not need to change. Provider
selection is controlled by `RESOURCE_PROVIDER` and `PLACEMENT_PROVIDER`.
