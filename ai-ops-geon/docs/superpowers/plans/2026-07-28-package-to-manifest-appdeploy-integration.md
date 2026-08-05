# Package-to-Manifest AppDeploy Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one geon workflow that uploads an application source to AppDeploy, registers the generated App Spec, automatically connects the issued `app_version_id` to the guarded Qwen Manifest ControlRun, and submits the approved Manifest through the existing AppDeploy boundary.

**Architecture:** geon receives the browser multipart request and streams the source to AppDeploy's existing Package API. A focused application workflow orchestrator records Package and App Registry evidence in the ControlRun, then invokes the existing Request Guard, Agent Registry, Dispatcher, Qwen Planner, and Manifest Guard pipeline. AppDeploy remains a separate service and retains Package building, App registration, Target selection, Runtime Adapter selection, and deployment execution.

**Tech Stack:** Go 1.22+, Echo v4, standard `net/http` and `mime/multipart`, Qwen through Ollama, vanilla HTML/CSS/JavaScript, Node browser tests with Playwright, Swagger/OpenAPI.

## Global Constraints

- Modify only `C:\Users\geonhae\Documents\Kyunghee-aiops-go`; do not modify the AppDeploy checkout.
- Preserve the existing JSON `POST /api/v1/control-runs` and `app_version_id` workflow.
- Add `POST /api/v1/control-runs/from-package` for multipart uploads.
- Default `requested_by` to `ai-agent`.
- Default geon upload limit to 50 MiB through `AIOPS_APP_UPLOAD_MAX_BYTES`.
- Do not store source bytes, credentials, SSH keys, or secrets in a ControlRun.
- Do not automatically submit a Manifest; enable submission only after `MANIFEST_APPROVED`.
- Preserve successful Package and App registration identifiers after a later failure.
- Treat `target_profile_id` as a hint; AppDeploy owns final Target selection.
- Use tests before production edits and commit each independently testable task.

---

## File Structure

### New files

- `go/service-control-api/internal/appdeploy/application_client.go`
  - Multipart Package upload and App registration HTTP methods.
- `go/service-control-api/internal/appdeploy/application_client_test.go`
  - AppDeploy request and response contract tests.
- `go/service-control-api/internal/api/application_workflow.go`
  - Package-to-ControlRun orchestration.
- `go/service-control-api/internal/api/application_workflow_test.go`
  - Workflow success, partial failure, and compatibility tests.
- `go/service-control-api/internal/api/application_workflow_api.go`
  - Echo multipart handler and HTTP status mapping.
- `go/service-control-api/internal/api/application_workflow_api_test.go`
  - Multipart endpoint contract tests.

### Modified files

- `go/service-control-api/internal/appdeploy/models.go`
  - Package/App contracts and latest Manifest requirements.
- `go/service-control-api/internal/appdeploy/manifest.go`
  - Runtime, cost policy, and App Spec compatibility validation.
- `go/service-control-api/internal/appdeploy/manifest_test.go`
  - New Manifest contract and Guard tests.
- `go/service-control-api/internal/api/config.go`
  - Upload limit configuration.
- `go/service-control-api/internal/api/server.go`
  - New route registration.
- `go/service-control-api/internal/api/control_run_service.go`
  - Split Run creation from the reusable guarded planning pipeline.
- `go/service-control-api/internal/api/control_run_models.go`
  - Deployment requirements accepted from JSON and multipart workflows.
- `go/service-control-api/internal/controlrun/models.go`
  - Application evidence, partial result, and Package/App lifecycle states.
- `go/service-control-api/internal/controlrun/store.go`
  - Deep-copy the new evidence fields.
- `go/service-control-api/internal/controlrun/store_test.go`
  - Evidence clone and persistence tests.
- `go/service-control-api/internal/deploymentplanner/generator.go`
  - Generate `spec.requirements` without changing trusted App identity.
- `go/service-control-api/internal/deploymentplanner/generator_test.go`
  - Requirements and cost policy generation tests.
- `go/service-control-api/internal/webui/static/index.html`
  - Upload/existing-App segmented input and result sections.
- `go/service-control-api/internal/webui/static/app.js`
  - Multipart submission, mode switching, and evidence rendering.
- `go/service-control-api/internal/webui/static/app.css`
  - Stable upload controls and responsive result layout.
- `go/service-control-api/internal/webui/static/manifest_stages.js`
  - Package and registration stages.
- `go/service-control-api/internal/webui/webui_test.go`
  - Static UI contract tests.
- `go/service-control-api/internal/webui/control_run_browser_test.js`
  - Desktop/mobile browser flow tests.
- `go/service-control-api/README.md`
  - Setup, API, experiment order, and failure recovery.
- `go/service-control-api/docs/swagger/swagger.yaml`
  - Generated OpenAPI contract.
- `go/service-control-api/docs/swagger/swagger.json`
  - Generated OpenAPI contract.

---

### Task 1: AppDeploy Package and App Registry Client

**Files:**
- Create: `go/service-control-api/internal/appdeploy/application_client.go`
- Create: `go/service-control-api/internal/appdeploy/application_client_test.go`
- Modify: `go/service-control-api/internal/appdeploy/models.go`

**Interfaces:**
- Produces:
  - `PackageUpload`
  - `PackageBuildResponse`
  - `AppRegistrationResponse`
  - `Client.BuildPackage(context.Context, PackageUpload) (PackageBuildResponse, error)`
  - `Client.RegisterApp(context.Context, json.RawMessage) (AppRegistrationResponse, error)`
- Consumes: existing `Client.doJSON`, `decodeAPIError`, and AppDeploy base URL.

- [ ] **Step 1: Write failing Package and registration client tests**

```go
func TestClientBuildPackageStreamsMultipartContract(t *testing.T) {
	var gotFile string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/artifacts/packages" {
			t.Fatalf("path = %s", r.URL.Path)
		}
		if err := r.ParseMultipartForm(1 << 20); err != nil {
			t.Fatal(err)
		}
		file, header, err := r.FormFile("source")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		content, _ := io.ReadAll(file)
		gotFile = string(content)
		if header.Filename != "run.sh" ||
			r.FormValue("package_type") != "script" ||
			r.FormValue("runtime_type") != "gpu" {
			t.Fatalf("unexpected multipart request")
		}
		w.Header().Set("content-type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{
		  "package_type":"script",
		  "artifact_uri":"file:///packages/app.tar.gz",
		  "archive_name":"app.tar.gz",
		  "checksum":"sha256:test",
		  "app_spec":{"kind":"AIApp"}
		}`)
	}))
	defer server.Close()

	client, _ := NewClient(server.URL+"/api/v1", nil)
	_, err := client.BuildPackage(context.Background(), PackageUpload{
		Source:      strings.NewReader("#!/bin/bash\n"),
		Filename:    "run.sh",
		PackageType: "script",
		AppName:     "gpu-app",
		AppVersion:  "0.1.0",
		Entrypoint:  "run.sh",
		RuntimeType: "gpu",
	})
	if err != nil || gotFile != "#!/bin/bash\n" {
		t.Fatalf("BuildPackage() err=%v file=%q", err, gotFile)
	}
}

func TestClientRegisterAppSendsOnlyAppSpec(t *testing.T) {
	// Assert POST /api/v1/apps body equals {"app_spec":{"kind":"AIApp"}}.
	// Return app_id and app_version_id and verify both are required.
}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cd go/service-control-api
go test ./internal/appdeploy -run 'TestClient(BuildPackage|RegisterApp)' -count=1
```

Expected: compile failure because Package/App client contracts do not exist.

- [ ] **Step 3: Add exact boundary models**

```go
type PackageUpload struct {
	Source          io.Reader
	Filename        string
	PackageType     string
	AppName         string
	AppVersion      string
	Entrypoint      string
	RuntimeType     string
	ServicePort     int
	HealthcheckPath string
}

type PackageBuildResponse struct {
	RequestID   string          `json:"request_id,omitempty"`
	PackageType string          `json:"package_type"`
	ArtifactURI string          `json:"artifact_uri"`
	ArchiveName string          `json:"archive_name"`
	SizeBytes   int64           `json:"size_bytes"`
	Checksum    string          `json:"checksum"`
	AppSpec     json.RawMessage `json:"app_spec"`
}

type AppRegistrationResponse struct {
	RequestID    string          `json:"request_id,omitempty"`
	AppID        string          `json:"app_id"`
	AppVersionID string          `json:"app_version_id"`
	Name         string          `json:"name"`
	Version      string          `json:"version"`
	AppSpec      json.RawMessage `json:"app_spec"`
}
```

- [ ] **Step 4: Implement streaming multipart and registration calls**

Use `io.Pipe` and `multipart.NewWriter` so source bytes are not duplicated in
memory. The goroutine writes scalar fields and then copies `Source` into the
`source` form part. The request must preserve the existing AppDeploy base path,
set `accept: application/json`, and use the writer's multipart content type.

`RegisterApp` calls:

```go
payload := struct {
	AppSpec json.RawMessage `json:"app_spec"`
}{AppSpec: appSpec}
err := client.doJSON(ctx, http.MethodPost, "/apps", payload, &response)
```

Reject an empty filename, nil source, missing `app_spec`, missing `app_id`, or
missing `app_version_id` before returning success.

- [ ] **Step 5: Run focused and package tests**

```powershell
go test ./internal/appdeploy -count=1
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/internal/appdeploy
git commit -m "feat: add AppDeploy application package client"
```

---

### Task 2: Latest Deployment Requirements and Guard Contract

**Files:**
- Modify: `go/service-control-api/internal/appdeploy/models.go`
- Modify: `go/service-control-api/internal/appdeploy/manifest.go`
- Modify: `go/service-control-api/internal/appdeploy/manifest_test.go`
- Modify: `go/service-control-api/internal/deploymentplanner/generator.go`
- Modify: `go/service-control-api/internal/deploymentplanner/generator_test.go`
- Modify: `go/service-control-api/internal/api/control_run_models.go`

**Interfaces:**
- Produces:
  - `DeploymentRequirements`
  - `DeploymentSpec.Requirements`
  - `CreateControlRunRequest.Requirements`
- Consumes: current `ResourceRequirements`, Qwen generator, and Manifest Guard.

- [ ] **Step 1: Write failing requirements tests**

Add tests that assert:

```go
requirements := DeploymentRequirements{
	Runtime:    "gpu",
	Resources: ResourceRequirements{CPU: "4", Memory: "8Gi", GPU: "1", Storage: "20Gi"},
	CostPolicy: "min_cost",
}
manifest := validManifest()
manifest.Spec.Requirements = &requirements
if err := ValidateManifest(manifest, ManifestConstraints{
	AppVersionID: "appver-test",
	RuntimeType:  "gpu",
}); err != nil {
	t.Fatal(err)
}
```

Also assert rejection for:

- `runtime=gpu` with `gpu=0`
- `runtime=cpu` with `gpu=1`
- `cost_policy=unknown`
- conflicting `spec.resources` and `requirements.resources`
- Qwen changing the trusted `app_version_id`

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/appdeploy ./internal/deploymentplanner -count=1
```

Expected: compile failure because `DeploymentRequirements` is absent.

- [ ] **Step 3: Extend the typed Manifest**

```go
type DeploymentRequirements struct {
	Runtime     string               `json:"runtime,omitempty"`
	Resources   ResourceRequirements `json:"resources,omitempty"`
	Accelerator string               `json:"accelerator,omitempty"`
	SLO         map[string]any       `json:"slo,omitempty"`
	CostPolicy  string               `json:"cost_policy,omitempty"`
	Labels      map[string]string    `json:"labels,omitempty"`
}

type DeploymentSpec struct {
	// Existing fields remain unchanged.
	Requirements *DeploymentRequirements `json:"requirements,omitempty"`
}
```

Extend `ManifestConstraints` with `RuntimeType string`. Normalize effective
resources so `spec.resources` and `requirements.resources` are identical after
generation. Allow an empty cost policy or `min_cost` only.

- [ ] **Step 4: Update Qwen trusted input and prompt**

Add `Requirements *appdeploy.DeploymentRequirements` to
`deploymentplanner.GenerateInput`. Include the exact trusted requirements in
the user payload and instruct Qwen to preserve them. After decoding, overwrite
the identity and trusted policy fields from the request before Guard
validation:

```go
manifest.Spec.AppVersionID = input.AppVersionID
manifest.Spec.RequestedBy = input.RequestedBy
if input.Requirements != nil {
	manifest.Spec.Requirements = cloneRequirements(input.Requirements)
	manifest.Spec.Resources = input.Requirements.Resources
}
```

Qwen may structure metadata and optional safe parameters, but may not alter
the App identity, runtime, resource envelope, or cost policy supplied by geon.

- [ ] **Step 5: Run focused tests**

```powershell
go test ./internal/appdeploy ./internal/deploymentplanner ./internal/api -run 'Manifest|Generator|ControlRun' -count=1
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/internal/appdeploy \
  go/service-control-api/internal/deploymentplanner \
  go/service-control-api/internal/api/control_run_models.go
git commit -m "feat: align Manifest deployment requirements"
```

---

### Task 3: ControlRun Application Evidence and Reusable Planning Pipeline

**Files:**
- Create: `go/service-control-api/internal/api/application_workflow.go`
- Create: `go/service-control-api/internal/api/application_workflow_test.go`
- Modify: `go/service-control-api/internal/api/control_run_service.go`
- Modify: `go/service-control-api/internal/controlrun/models.go`
- Modify: `go/service-control-api/internal/controlrun/store.go`
- Modify: `go/service-control-api/internal/controlrun/store_test.go`

**Interfaces:**
- Consumes:
  - `appdeploy.Client.BuildPackage`
  - `appdeploy.Client.RegisterApp`
  - reusable guarded planning pipeline
- Produces:
  - `CreatePackageControlRunInput`
  - `applicationProvisioner`
  - `Service.CreateControlRunFromPackage`
  - ControlRun `Application` and `PartialResult` evidence.

- [ ] **Step 1: Write failing orchestration tests**

Create a fake provisioner:

```go
type fakeApplicationProvisioner struct {
	packageResult appdeploy.PackageBuildResponse
	appResult     appdeploy.AppRegistrationResponse
	packageErr    error
	appErr        error
}

func (fake *fakeApplicationProvisioner) BuildPackage(
	context.Context,
	appdeploy.PackageUpload,
) (appdeploy.PackageBuildResponse, error) {
	return fake.packageResult, fake.packageErr
}

func (fake *fakeApplicationProvisioner) RegisterApp(
	context.Context,
	json.RawMessage,
) (appdeploy.AppRegistrationResponse, error) {
	return fake.appResult, fake.appErr
}
```

Tests must verify:

1. Package success and App registration success put the issued
   `app_version_id` into `run.Request` and `run.Manifest`.
2. Package failure produces `PACKAGE_FAILED` and no registration call.
3. Registration failure produces `APP_REGISTRATION_FAILED` and retains
   `artifact_uri`, `archive_name`, and checksum.
4. Manifest rejection retains Package and App registration evidence.
5. Existing `CreateControlRun` still creates an approved Run without
   application evidence.

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/controlrun ./internal/api -run 'Application|ControlRun' -count=1
```

Expected: compile failure for missing application workflow types.

- [ ] **Step 3: Add ControlRun lifecycle and evidence**

```go
const (
	StatusPackaging             Status = "PACKAGING"
	StatusPackageFailed         Status = "PACKAGE_FAILED"
	StatusRegisteringApp        Status = "REGISTERING_APP"
	StatusAppRegistrationFailed Status = "APP_REGISTRATION_FAILED"
)

type ApplicationEvidence struct {
	Package      *appdeploy.PackageBuildResponse    `json:"package,omitempty"`
	Registration *appdeploy.AppRegistrationResponse `json:"registration,omitempty"`
	AppSpec      json.RawMessage                    `json:"app_spec,omitempty"`
}

type PartialResult struct {
	ArtifactURI  string `json:"artifact_uri,omitempty"`
	ArchiveName  string `json:"archive_name,omitempty"`
	Checksum     string `json:"checksum,omitempty"`
	AppID        string `json:"app_id,omitempty"`
	AppVersionID string `json:"app_version_id,omitempty"`
}
```

Add `Application *ApplicationEvidence` and
`PartialResult *PartialResult` to `controlrun.Run`. Update store cloning to
deep-copy raw JSON and pointer fields.

- [ ] **Step 4: Refactor the current planning pipeline**

Split `CreateControlRunWithDependencies` into:

```go
func (service Service) createEmptyControlRun(request CreateControlRunRequest) (controlrun.Run, error)

func (service Service) continueGuardedManifestPlanning(
	ctx context.Context,
	runID string,
	request CreateControlRunRequest,
	candidatesPath string,
	guardPolicyPath string,
	generator controlRunManifestGenerator,
) (controlrun.Run, error)
```

The existing public method creates one Run and calls the continuation. The
Package workflow creates the Run first, records Package/App stages, injects the
issued `app_version_id`, then calls the same continuation. No duplicate Guard,
Registry, Dispatcher, Qwen, or Manifest logic is allowed.

- [ ] **Step 5: Implement application orchestration**

```go
type applicationProvisioner interface {
	BuildPackage(context.Context, appdeploy.PackageUpload) (appdeploy.PackageBuildResponse, error)
	RegisterApp(context.Context, json.RawMessage) (appdeploy.AppRegistrationResponse, error)
}

type CreatePackageControlRunInput struct {
	Package      appdeploy.PackageUpload
	Planner      CreateControlRunRequest
	Requirements appdeploy.DeploymentRequirements
}
```

Stage order:

1. `app_upload`
2. `package_build`
3. `app_registration`
4. existing guarded Manifest stages

Do not add `source` bytes or filenames containing local paths to stage details.

- [ ] **Step 6: Run focused tests**

```powershell
go test ./internal/controlrun ./internal/api -run 'Application|ControlRun' -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add go/service-control-api/internal/controlrun \
  go/service-control-api/internal/api/application_workflow.go \
  go/service-control-api/internal/api/application_workflow_test.go \
  go/service-control-api/internal/api/control_run_service.go
git commit -m "feat: orchestrate package registration ControlRuns"
```

---

### Task 4: Multipart ControlRun Endpoint

**Files:**
- Create: `go/service-control-api/internal/api/application_workflow_api.go`
- Create: `go/service-control-api/internal/api/application_workflow_api_test.go`
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/server.go`

**Interfaces:**
- Consumes: `Service.CreateControlRunFromPackage`.
- Produces: `POST /api/v1/control-runs/from-package`.

- [ ] **Step 1: Write failing HTTP tests**

Build multipart requests with `multipart.NewWriter`. Test:

- successful script upload returns 201 and `MANIFEST_APPROVED`
- missing source returns 400
- empty source returns 400
- oversized source returns 413
- invalid numeric `service_port` returns 400
- Package failure returns a Run with `PACKAGE_FAILED`
- registration failure returns a Run with `APP_REGISTRATION_FAILED`

The success fake AppDeploy server must implement:

```text
POST /api/v1/artifacts/packages
POST /api/v1/apps
```

and the fake Qwen endpoint must return a valid Manifest.

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/api -run 'ControlRunFromPackage' -count=1
```

Expected: 404 or missing handler compile failure.

- [ ] **Step 3: Add upload configuration**

Add `AppUploadMaxBytes int64` to `ServerConfig`. Read
`AIOPS_APP_UPLOAD_MAX_BYTES`, default to `50 << 20`, reject non-positive
configured values by falling back to the default, and cap at `1 << 30`.

- [ ] **Step 4: Implement multipart binding**

Register:

```go
server.POST(pathControlRuns+"/from-package", handler.RestPostControlRunFromPackage)
```

The handler must:

1. Wrap the body with `http.MaxBytesReader`.
2. Parse multipart with an 8 MiB memory threshold.
3. defer `MultipartForm.RemoveAll()`.
4. open and defer-close `source`.
5. parse numeric fields with explicit validation.
6. construct trusted `DeploymentRequirements`.
7. call `CreateControlRunFromPackage`.
8. return the ControlRun-specific status code.

Map:

- `MANIFEST_APPROVED` -> 201
- `PACKAGE_FAILED` -> 502
- `APP_REGISTRATION_FAILED` -> 502
- Request/Agent/Manifest reject -> existing 400/403/422 mapping
- body too large -> 413

- [ ] **Step 5: Run API and all Go tests**

```powershell
go test ./internal/api -count=1
go test ./... -count=1
go vet ./...
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/internal/api
git commit -m "feat: expose package ControlRun API"
```

---

### Task 5: User-First Upload Web Workflow

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/static/manifest_stages.js`
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/control_run_browser_test.js`

**Interfaces:**
- Consumes:
  - `POST /api/v1/control-runs`
  - `POST /api/v1/control-runs/from-package`
  - `POST /api/v1/control-runs/{run_id}/submit`
- Produces: one visible workflow with upload and existing-App modes.

- [ ] **Step 1: Write failing static and browser tests**

Static tests must require:

```text
input-mode-upload
input-mode-existing
application-source
package-type
app-name
app-version
entrypoint
runtime-type
cost-policy
application-package-result
application-registration-result
```

Browser tests must verify:

1. Upload mode is the default.
2. Existing-App mode hides file fields and requires `app_version_id`.
3. Upload mode sends `FormData` to `/api/v1/control-runs/from-package`.
4. Returned `app_version_id` is rendered without manual copying.
5. Package/App stages precede `사용자 요청`.
6. `Submit to AppDeploy` is disabled until `MANIFEST_APPROVED`.
7. Desktop 1440x900 and mobile 390x844 have no overlapping controls.

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/webui -count=1
$env:NODE_PATH="C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
& "C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --test internal/webui/control_run_browser_test.js
```

Expected: missing DOM contract and browser assertions fail.

- [ ] **Step 3: Add the upload/existing segmented control**

Use radio inputs styled as a segmented control:

```html
<fieldset class="input-mode-control">
  <legend>앱 입력 방식</legend>
  <label><input id="input-mode-upload" type="radio" name="input_mode" value="upload" checked>새 앱 업로드</label>
  <label><input id="input-mode-existing" type="radio" name="input_mode" value="existing">등록된 앱 사용</label>
</fieldset>
```

Use one form. Upload-only and existing-only field groups toggle with `hidden`;
required attributes must toggle at the same time.

- [ ] **Step 4: Submit the correct request type**

In upload mode:

```js
const body = new FormData(form);
const run = await api("/api/v1/control-runs/from-package", {
  method: "POST",
  body,
  rawBody: true,
});
```

Update the shared `api` helper so `rawBody: true` does not set
`content-type: application/json`; the browser supplies the multipart boundary.

In existing mode, preserve the current JSON request exactly.

- [ ] **Step 5: Render application evidence and stage order**

Set the upload stage order to:

```js
[
  "app_upload",
  "package_build",
  "app_registration",
  "user_request",
  "request_guard",
  "agent_registry",
  "agent_dispatch",
  "qwen_planner",
  "manifest_guard",
]
```

Existing-App Runs render the first three stages as `skipped`, not `pending`.
Display Package URI/checksum, `app_id`, and `app_version_id` in compact result
rows. Do not display uploaded local paths or source bytes.

- [ ] **Step 6: Add responsive styling**

Use stable grid tracks, 36-40px controls, and a single column below 760px.
Long filenames and identifiers must use wrapping or ellipsis without resizing
buttons. No nested cards or marketing hero layout.

- [ ] **Step 7: Run web tests and inspect screenshots**

```powershell
go test ./internal/webui -count=1
$env:NODE_PATH="C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
& "C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --test internal/webui/control_run_browser_test.js
```

Capture desktop and mobile screenshots under `output/playwright/`, inspect them,
then remove the temporary artifacts before committing.

- [ ] **Step 8: Commit**

```powershell
git add go/service-control-api/internal/webui
git commit -m "feat: add application upload Manifest workflow"
```

---

### Task 6: OpenAPI, README, and Demonstration Contract

**Files:**
- Modify: `go/service-control-api/README.md`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`

**Interfaces:**
- Documents the public multipart endpoint and the unchanged submit endpoint.

- [ ] **Step 1: Write failing OpenAPI contract assertions**

Require both generated files to contain:

```text
/api/v1/control-runs/from-package
multipart/form-data
source
package_type
natural_language_request
cost_policy
```

Also retain `/api/v1/control-runs/{run_id}/submit`.

- [ ] **Step 2: Run the OpenAPI test and verify RED**

```powershell
go test ./internal/api -run OpenAPI -count=1
```

Expected: missing endpoint contract.

- [ ] **Step 3: Add Swagger annotations and regenerate**

Annotate `RestPostControlRunFromPackage` with `@Accept multipart/form-data`,
all form parameters, 201/400/403/413/422/502 responses, and the exact route.

From the repository root run:

```powershell
make swag
```

If `make` is unavailable on Windows, run the repository's documented `swag init`
command from `Makefile` with the same input and output paths.

- [ ] **Step 4: Update the operator guide**

Document:

```text
Ollama + Qwen
AppDeploy :8080
geon :18080
새 앱 업로드
Package 생성
App 등록
app_version_id 자동 연결
Manifest 생성 및 Guard 승인
AppDeploy 제출
상태와 로그 확인
```

Include a `curl` multipart example and clarify that Package/App successes are
not automatically rolled back after a later failure.

- [ ] **Step 5: Run documentation and full tests**

```powershell
go test ./... -count=1
go vet ./...
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/README.md \
  go/service-control-api/docs/swagger \
  go/service-control-api/internal/api
git commit -m "docs: document package to Manifest workflow"
```

---

### Task 7: End-to-End Verification and Branch Publication

**Files:**
- No production file changes unless verification exposes a defect.

**Interfaces:**
- Verifies the complete geon-to-AppDeploy contract without changing AppDeploy.

- [ ] **Step 1: Run all automated verification**

```powershell
cd C:\Users\geonhae\Documents\Kyunghee-aiops-go\go\service-control-api
go test ./... -count=1
go vet ./...

cd ..\aiops-guard
go test ./... -count=1
go vet ./...
```

Run the Node browser suite from `internal/webui`. Expected: all PASS.

- [ ] **Step 2: Start the current AppDeploy server**

Use the unmodified checkout:

```powershell
cd C:\Users\geonhae\Documents\Codex\2026-07-09\new-chat\outputs\ai-ops-AppDeployer\AppDeploy
$env:AIAPP_SERVER_PORT="8080"
go run ./cmd/web
```

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/v1/healthz
```

- [ ] **Step 3: Start geon with Qwen**

```powershell
cd C:\Users\geonhae\Documents\Kyunghee-aiops-go\go\service-control-api
$env:AIOPS_REPO_ROOT="C:\Users\geonhae\Documents\Kyunghee-aiops-go"
$env:AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
$env:AIOPS_PLANNER_GUARD_POLICY_PATH="config/planner_guard_policy.json"
$env:AIOPS_APPDEPLOY_BASE_URL="http://127.0.0.1:8080/api/v1"
$env:AIOPS_BIND_ADDRESS="127.0.0.1"
$env:PORT="18080"
go run ./cmd/service-control-api
```

- [ ] **Step 4: Execute the real local workflow**

Upload `run.sh` with:

```text
package_type=script
app_name=geon-package-demo
app_version=0.1.0
entrypoint=run.sh
runtime_type=gpu
cpu=4
memory=8Gi
gpu=1
storage=20Gi
cost_policy=min_cost
```

Verify the returned Run contains:

```text
application.package.artifact_uri
application.registration.app_id
application.registration.app_version_id
manifest.spec.app_version_id == application.registration.app_version_id
manifest.spec.requirements.cost_policy == min_cost
status == MANIFEST_APPROVED
```

This local verification proves Package, App Registry, Qwen Planner, and Guard
integration. Actual VM deployment is claimed only after a registered reachable
Target accepts the separate submit call and returns `deployment_id`.

- [ ] **Step 5: Verify working tree scope**

```powershell
git status --short --branch
git diff --check origin/geon...HEAD
```

Confirm `config/inference_optimization.json` remains untracked and untouched.

- [ ] **Step 6: Push the existing branch**

```powershell
git push origin geon
```

Expected: the existing `geon` branch advances; no new branch or PR is created.
