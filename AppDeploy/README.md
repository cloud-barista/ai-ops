# AI App Deployer prototype

This module is a headless Go/Echo deployment service. It does not provide or
depend on a web application or web console. An AI automation agent sends the
original application to App Management and sends a deployment request that
contains the resolved resource, runtime, SLO, label, and cost requirements.

## Run the local prototype

From `AppDeploy`:

Go 1.25 or newer is required; Go 1.25.0 is the verified workspace toolchain.

```powershell
$env:RESOURCE_PROVIDER = "local"
$env:PLACEMENT_PROVIDER = "local"
$env:AIAPP_STORE_PATH = ".\tmp\local-demo-store.json"
go run ./cmd/server
```

The service listens on `http://localhost:8080` by default. API operations use
the `/api/v1` prefix. The contract is served at `/openapi.yaml`; `/swagger`
serves the existing static contract viewer only and is not a web console.

For a complete local process deployment, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-demo.ps1
```

The demo registers an application and a local VM record, creates a request,
shows the deterministic placement decision and runtime logs, then stops the
process and releases its reservation.

## Request flow

1. `POST /api/v1/apps` validates and stores the App Spec and the original JSON
   document. Artifact types remain `package`, `git`, `binary`, and `script`.
2. `POST /api/v1/target-profiles` registers a VM/node record. In local mode
   this is the Local VM Registry record and includes capacity, status, labels,
   supported runtimes, cost weight, and current allocation.
3. `POST /api/v1/deployments` accepts an existing `app_version_id` plus a
   `requirements` envelope. Missing resource values are filled from the
   registered App Spec. `cost_policy` defaults to `min_cost`.
4. The local Placement Provider filters READY nodes, checks quantities,
   accelerator type, runtime, and labels, reserves capacity, and selects by
   cost, remaining capacity, then VM ID.
5. The Orchestrator invokes the selected Runtime Adapter. A `local_process`
   target executes the command with `os/exec`; CPU/GPU/ETRI adapters retain
   their existing boundaries.

The stored `original_application` is the exact JSON document received by
`POST /api/v1/apps`. A local process may finish after creation; the next
deployment status or log request reconciles `COMPLETED`/`RUNTIME_FAILED` and
releases its reservation. `POST .../stop` performs `STOPPING` -> `STOPPED` and
also releases the reservation.

Useful endpoints:

```text
POST /api/v1/apps
GET  /api/v1/target-profiles
POST /api/v1/deployments
GET  /api/v1/deployments/{deployment_id}
GET  /api/v1/deployments/{deployment_id}/logs
POST /api/v1/deployments/{deployment_id}/stop
```

Example deployment request:

```json
{
  "app_version_id": "appver-...",
  "requested_by": "ai-agent",
  "requirements": {
    "resources": { "cpu": "1", "memory": "256Mi", "storage": "1Gi" },
    "runtime": "cpu",
    "slo": { "availability": "99.9%" },
    "cost_policy": "min_cost",
    "labels": { "zone": "local" }
  }
}
```

The response from `GET /api/v1/deployments/{deployment_id}` contains
`placement.target_vm_id`, `placement.allocation`, `placement.source`,
`placement.score`, and `placement.reason`.

## Provider configuration

```text
RESOURCE_PROVIDER=local
PLACEMENT_PROVIDER=local
```

The future ETRI selection is explicit:

```text
RESOURCE_PROVIDER=etri
PLACEMENT_PROVIDER=etri
ETRI_RESOURCE_ENDPOINT=https://provided-by-etri
ETRI_PLACEMENT_ENDPOINT=https://provided-by-etri
ETRI_CREDENTIAL_REF=cred://etri/resource-provider
ETRI_TIMEOUT=30s
```

ETRI endpoint paths, request DTOs, authentication behavior, and response
mapping are intentionally not invented. Missing endpoint configuration is an
error and never falls back to local mode. The current implementations are
`internal/provider/ETRIResourceMetadataAdapter` and
`internal/provider/ETRIPlacementAdapter`; they are contract stubs ready for
the partner DTO mappers.

## Tests

```powershell
go test ./...
go vet ./...
```

If the local Go installation cannot write its user telemetry/cache directory,
use a workspace cache for the command:

```powershell
$env:GOCACHE = ".\.gocache"
go test ./...
```

Docker, Kubernetes, OCI images, and container registries are deliberately out
of scope.
