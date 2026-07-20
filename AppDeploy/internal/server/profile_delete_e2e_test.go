package server_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestDeleteProfilesWithStoppedHistoryPreservesEvidenceAndCredentialE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)

	secret := "profile-delete-credential-secret"
	credentialID := "profile-delete-credential"
	credentialRef := "cred://runtime/" + credentialID
	credentialRecorder := localCredentialRequest(t, e, http.MethodPost, "/api/v1/credentials", map[string]any{
		"credential_id":        credentialID,
		"credential_type":      "ssh",
		"ssh_user":             "vm-user",
		"auth_type":            "password",
		"password":             secret,
		"host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
	})
	assertStatus(t, credentialRecorder, http.StatusCreated)
	assertCredentialSecretAbsent(t, credentialRecorder.Body.Bytes(), secret)

	targetProfile := profileDeleteTargetE2E("target-profile-delete", "target delete e2e", credentialRef)
	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", targetProfile)

	check := postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{
		TargetProfileID: targetProfile.TargetProfileID,
	})
	if check.Status != "available" {
		t.Fatalf("resource check status = %s, want available", check.Status)
	}

	app := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: validMockApp()})
	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:    app.AppVersionID,
		TargetProfileID: targetProfile.TargetProfileID,
	})
	metric := postJSON[model.InferenceMetricRecord](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/metrics", model.InferenceMetricCreateRequest{
		LatencyMS:    8.5,
		RequestCount: 3,
	})
	stopped := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments/"+deployment.DeploymentID+"/stop", nil)
	if stopped.Status != model.StatusStopped {
		t.Fatalf("deployment status = %s, want %s", stopped.Status, model.StatusStopped)
	}

	deletedTarget := postJSON[model.ProfileDeleteResponse](t, e, http.MethodDelete, "/api/v1/target-profiles/"+targetProfile.TargetProfileID, nil)
	assertProfileDeleteResponse(t, deletedTarget, model.ProfileTypeTarget, targetProfile.TargetProfileID, targetProfile.Name, true)

	assertAPIError(t, request(t, e, http.MethodDelete, "/api/v1/target-profiles/"+targetProfile.TargetProfileID, nil), http.StatusNotFound, "NOT_FOUND")
	assertTargetAbsentFromLists(t, e, targetProfile.TargetProfileID)
	assertTargetInventoryAbsentE2E(t, e, targetProfile.TargetProfileID)

	preservedDeployment := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+deployment.DeploymentID)
	if preservedDeployment.Status != model.StatusStopped || preservedDeployment.TargetProfileID != targetProfile.TargetProfileID {
		t.Fatalf("STOPPED deployment history was not preserved: %+v", preservedDeployment)
	}
	logs := getJSON[struct {
		Items []model.DeploymentLog `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/logs")
	if len(logs.Items) == 0 {
		t.Fatal("deployment events were not preserved")
	}
	metrics := getJSON[struct {
		Items []model.InferenceMetricRecord `json:"items"`
	}](t, e, "/api/v1/deployments/"+deployment.DeploymentID+"/metrics")
	if len(metrics.Items) != 1 || metrics.Items[0].MetricID != metric.MetricID {
		t.Fatalf("deployment metrics were not preserved: %+v", metrics.Items)
	}

	credentialsRecorder := localCredentialRequest(t, e, http.MethodGet, "/api/v1/credentials", nil)
	assertStatus(t, credentialsRecorder, http.StatusOK)
	assertCredentialSecretAbsent(t, credentialsRecorder.Body.Bytes(), secret)
	var credentials struct {
		Items []credentialAPIRecord `json:"items"`
	}
	decodeRecorder(t, credentialsRecorder, &credentials)
	if len(credentials.Items) != 1 || credentials.Items[0].CredentialID != credentialID || credentials.Items[0].CredentialRef != credentialRef {
		t.Fatalf("credential was not retained after target deletion: %+v", credentials.Items)
	}

	targetWithoutInventory := profileDeleteTargetE2E("target-without-inventory", "", credentialRef)
	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", targetWithoutInventory)
	deletedWithoutInventory := postJSON[model.ProfileDeleteResponse](t, e, http.MethodDelete, "/api/v1/target-profiles/"+targetWithoutInventory.TargetProfileID, nil)
	assertProfileDeleteResponse(t, deletedWithoutInventory, model.ProfileTypeTarget, targetWithoutInventory.TargetProfileID, "", false)
}

func TestDeleteProfilesWithRunningDeploymentReturnsConflictE2E(t *testing.T) {
	t.Setenv("AIAPP_CPUVM_RUNNER", "dry-run")
	t.Setenv("AIAPP_GPUVM_RUNNER", "dry-run")
	e := newTestServer(t)

	targetProfile := profileDeleteTargetE2E("target-profile-blocked", "blocked target", "")
	postJSON[model.TargetProfile](t, e, http.MethodPost, "/api/v1/target-profiles", targetProfile)
	postJSON[model.ResourceCheckResponse](t, e, http.MethodPost, "/api/v1/resources/check", model.ResourceCheckRequest{TargetProfileID: targetProfile.TargetProfileID})
	app := postJSON[model.AppResponse](t, e, http.MethodPost, "/api/v1/apps", model.AppCreateRequest{AppSpec: validMockApp()})
	deployment := postJSON[model.DeploymentResponse](t, e, http.MethodPost, "/api/v1/deployments", model.DeploymentCreateRequest{
		AppVersionID:    app.AppVersionID,
		TargetProfileID: targetProfile.TargetProfileID,
	})
	if deployment.Status != model.StatusRunning {
		t.Fatalf("deployment status = %s, want %s", deployment.Status, model.StatusRunning)
	}

	targetDelete := request(t, e, http.MethodDelete, "/api/v1/target-profiles/"+targetProfile.TargetProfileID, nil)
	assertProfileDeleteConflictE2E(t, targetDelete, model.ErrTargetProfileInvalid, 1)

	targets := getJSON[struct {
		Items []model.TargetProfile `json:"items"`
	}](t, e, "/api/v1/target-profiles")
	if len(targets.Items) != 1 || targets.Items[0].TargetProfileID != targetProfile.TargetProfileID {
		t.Fatalf("target profile changed after rejected delete: %+v", targets.Items)
	}
	assertTargetInventoryPresentE2E(t, e, targetProfile.TargetProfileID)
	if got := getJSON[model.DeploymentResponse](t, e, "/api/v1/deployments/"+deployment.DeploymentID); got.Status != model.StatusRunning {
		t.Fatalf("deployment changed after rejected profile delete: %+v", got)
	}
}

func profileDeleteTargetE2E(id, name, credentialRef string) model.TargetProfile {
	return model.TargetProfile{
		TargetProfileID: id,
		Name:            name,
		CSP:             "mock",
		VM:              model.VMProfile{CredentialRef: credentialRef},
		Runtime: model.TargetRuntime{
			RuntimeType:   "mock",
			Accelerator:   "none",
			OperatingMode: "local_mock",
		},
	}
}

func assertProfileDeleteResponse(t *testing.T, response model.ProfileDeleteResponse, profileType, profileID, name string, inventoryDeleted bool) {
	t.Helper()
	if response.RequestID != "req-test-001" || response.ProfileType != profileType || response.ProfileID != profileID || response.Name != name {
		t.Fatalf("unexpected profile delete response: %+v", response)
	}
	if !response.Deleted || response.InventoryDeleted != inventoryDeleted || response.DeletedAt.IsZero() {
		t.Fatalf("unexpected profile delete result fields: %+v", response)
	}
}

func assertProfileDeleteConflictE2E(t *testing.T, recorder *httptest.ResponseRecorder, errorCode string, deploymentCount float64) {
	t.Helper()
	if recorder.Code != http.StatusConflict {
		t.Fatalf("status = %d, want %d body=%s", recorder.Code, http.StatusConflict, recorder.Body.String())
	}
	var body model.ErrorResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.RequestID != "req-test-001" || body.Error.Code != errorCode {
		t.Fatalf("unexpected error response: %+v", body)
	}
	if count, ok := body.Error.Details["deployment_count"].(float64); !ok || count != deploymentCount {
		t.Fatalf("deployment_count = %#v, want %g", body.Error.Details["deployment_count"], deploymentCount)
	}
	if !strings.Contains(body.Error.Message, "STOPPED") {
		t.Fatalf("message = %q, want STOPPED guidance", body.Error.Message)
	}
}

func assertTargetAbsentFromLists(t *testing.T, handler http.Handler, targetID string) {
	t.Helper()
	targets := getJSON[struct {
		Items []model.TargetProfile `json:"items"`
	}](t, handler, "/api/v1/target-profiles")
	for _, item := range targets.Items {
		if item.TargetProfileID == targetID {
			t.Fatalf("deleted target profile remains in list: %+v", item)
		}
	}
}

func assertTargetInventoryAbsentE2E(t *testing.T, handler http.Handler, targetID string) {
	t.Helper()
	inventory := getJSON[struct {
		Items []model.ResourceInventory `json:"items"`
	}](t, handler, "/api/v1/resources/inventory")
	for _, item := range inventory.Items {
		if item.TargetProfileID == targetID {
			t.Fatalf("deleted target inventory remains: %+v", item)
		}
	}
}

func assertTargetInventoryPresentE2E(t *testing.T, handler http.Handler, targetID string) {
	t.Helper()
	inventory := getJSON[struct {
		Items []model.ResourceInventory `json:"items"`
	}](t, handler, "/api/v1/resources/inventory")
	for _, item := range inventory.Items {
		if item.TargetProfileID == targetID {
			return
		}
	}
	t.Fatalf("target inventory %s is missing", targetID)
}
