package server_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestDeleteAppE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)

	created := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: validMockApp()})
	rec := request(t, e, http.MethodDelete, "/api/v1/apps/"+created.AppID, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("delete status = %d, want %d body=%s", rec.Code, http.StatusOK, rec.Body.String())
	}
	var deleted model.AppDeleteResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &deleted); err != nil {
		t.Fatalf("decode delete response: %v body=%s", err, rec.Body.String())
	}
	if deleted.RequestID != "req-test-001" || deleted.AppID != created.AppID || deleted.AppVersionID != created.AppVersionID {
		t.Fatalf("unexpected delete response: %+v", deleted)
	}
	if deleted.Name != created.Name || deleted.Version != created.Version || !deleted.Deleted || deleted.ArtifactDeleted || deleted.DeletedAt.IsZero() {
		t.Fatalf("unexpected delete result fields: %+v", deleted)
	}

	getDeleted := request(t, e, http.MethodGet, "/api/v1/apps/"+created.AppID, nil)
	assertAPIError(t, getDeleted, http.StatusNotFound, "NOT_FOUND")

	list := getJSON[struct {
		Items []model.AppResponse `json:"items"`
	}](t, e, "/api/v1/apps")
	for _, item := range list.Items {
		if item.AppID == created.AppID || item.AppVersionID == created.AppVersionID {
			t.Fatalf("deleted app remains in list: %+v", item)
		}
	}
}

func TestDeleteMissingAppE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)

	rec := request(t, e, http.MethodDelete, "/api/v1/apps/app-missing", nil)
	assertAPIError(t, rec, http.StatusNotFound, "NOT_FOUND")
}

func TestDeleteAppWithStoppedDeploymentHistoryE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)
	created := createMockAppForDeleteE2E(t, e)
	deployment := createMockDeploymentForDeleteE2E(t, e, created.AppVersionID)
	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("stop status = %s, want %s", stopped.Status, model.StatusStopped)
	}

	deleted := postJSON[model.AppDeleteResponse](t, e, http.MethodDelete, "/api/v1/apps/"+created.AppID, nil)
	if !deleted.Deleted || deleted.AppID != created.AppID || deleted.AppVersionID != created.AppVersionID {
		t.Fatalf("unexpected delete response: %+v", deleted)
	}
	assertAPIError(t, request(t, e, http.MethodGet, "/api/v1/apps/"+created.AppID, nil), http.StatusNotFound, "NOT_FOUND")

	preserved := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+deployment.DeploymentID)
	if preserved.Status != model.StatusStopped || preserved.AppVersionID != created.AppVersionID {
		t.Fatalf("STOPPED history was not preserved: %+v", preserved)
	}
	repeatedStop := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if repeatedStop.Status != model.StatusStopped {
		t.Fatalf("repeated stop after App deletion status = %s, want %s", repeatedStop.Status, model.StatusStopped)
	}
}

func TestDeleteAppWithRunningDeploymentReturnsConflictE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)
	created := createMockAppForDeleteE2E(t, e)
	deployment := createMockDeploymentForDeleteE2E(t, e, created.AppVersionID)

	assertDeleteConflictE2E(t, e, created.AppID, 1)
	if got := getJSON[model.AppResponse](t, e, "/api/v1/apps/"+created.AppID); got.AppVersionID != created.AppVersionID {
		t.Fatalf("App was not preserved: %+v", got)
	}
	if got := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+deployment.DeploymentID); got.Status != model.StatusRunning {
		t.Fatalf("RUNNING deployment was not preserved: %+v", got)
	}
}

func TestDeleteAppWithFailedDeploymentReturnsConflictE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)
	created := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: validMockApp()})
	failedCreate := request(t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     created.AppVersionID,
		RuntimeProfileID: "rt-missing",
		TargetProfileID:  "target-missing",
	})
	assertAPIError(t, failedCreate, http.StatusBadRequest, model.ErrTargetProfileInvalid)

	deployments := getJSON[struct {
		Items []model.DeploymentResponse `json:"items"`
	}](t, e, "/api/v1/deployments")
	var failed model.DeploymentResponse
	for _, item := range deployments.Items {
		if item.AppVersionID == created.AppVersionID {
			failed = item
			break
		}
	}
	if failed.DeploymentID == "" || failed.Status != model.StatusValidationFailed {
		t.Fatalf("failed deployment history not found: %+v", deployments.Items)
	}

	assertDeleteConflictE2E(t, e, created.AppID, 1)
	if got := getJSON[model.AppResponse](t, e, "/api/v1/apps/"+created.AppID); got.AppVersionID != created.AppVersionID {
		t.Fatalf("App was not preserved: %+v", got)
	}
	if got := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+failed.DeploymentID); got.Status != model.StatusValidationFailed {
		t.Fatalf("failed deployment was not preserved: %+v", got)
	}
}

func TestDeleteAppWithMixedStoppedAndRunningHistoryReturnsConflictE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)
	created := createMockAppForDeleteE2E(t, e)
	stopped := createMockDeploymentForDeleteE2E(t, e, created.AppVersionID)
	stopped = postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+stopped.DeploymentID+"/stop", nil)
	running := createMockDeploymentForDeleteE2E(t, e, created.AppVersionID)

	assertDeleteConflictE2E(t, e, created.AppID, 1)
	if got := getJSON[model.AppResponse](t, e, "/api/v1/apps/"+created.AppID); got.AppVersionID != created.AppVersionID {
		t.Fatalf("App was not preserved: %+v", got)
	}
	if got := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+stopped.DeploymentID); got.Status != model.StatusStopped {
		t.Fatalf("STOPPED deployment was not preserved: %+v", got)
	}
	if got := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+running.DeploymentID); got.Status != model.StatusRunning {
		t.Fatalf("RUNNING deployment was not preserved: %+v", got)
	}
}

func createMockAppForDeleteE2E(t *testing.T, e http.Handler) model.AppResponse {
	t.Helper()
	created := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: validMockApp()})
	createMockRuntimeProfile(t, e)
	createMockTargetProfile(t, e)
	return created
}

func createMockDeploymentForDeleteE2E(t *testing.T, e http.Handler, appVersionID string) model.DeploymentResponse {
	t.Helper()
	return postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:     appVersionID,
		RuntimeProfileID: "rt-mock-001",
		TargetProfileID:  "target-mock-001",
	})
}

func assertDeleteConflictE2E(t *testing.T, e http.Handler, appID string, wantBlockingCount float64) {
	t.Helper()
	rec := request(t, e, http.MethodDelete, "/api/v1/apps/"+appID, nil)
	assertAPIError(t, rec, http.StatusConflict, model.ErrAppSpecInvalid)
	var errorResponse model.ErrorResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &errorResponse); err != nil {
		t.Fatal(err)
	}
	if count, ok := errorResponse.Error.Details["deployment_count"].(float64); !ok || count != wantBlockingCount {
		t.Fatalf("deployment_count = %#v, want %g", errorResponse.Error.Details["deployment_count"], wantBlockingCount)
	}
	if !strings.Contains(errorResponse.Error.Message, "STOPPED") {
		t.Fatalf("message = %q, want STOPPED guidance", errorResponse.Error.Message)
	}
}

func assertAPIError(t *testing.T, rec *httptest.ResponseRecorder, wantStatus int, wantCode string) {
	t.Helper()
	if rec.Code != wantStatus {
		t.Fatalf("status = %d, want %d body=%s", rec.Code, wantStatus, rec.Body.String())
	}
	var body model.ErrorResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode error response: %v", err)
	}
	if body.RequestID != "req-test-001" || body.Error.Code != wantCode {
		t.Fatalf("error response = %+v, want request_id=req-test-001 code=%s", body, wantCode)
	}
}
