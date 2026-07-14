package server_test

import (
	"bytes"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/khu/ai-app-deployer/internal/model"
)

func TestTargetCredentialRefRejectsSecretMaterialBeforePersistence(t *testing.T) {
	storePath := filepath.Join(t.TempDir(), "store.json")
	e := newCredentialTestServer(t, storePath, false)
	secret := "credential-ref-secret-canary"

	recorder := localCredentialRequest(t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-secret-guard",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "vm.example.invalid",
			SSHPort:       22,
			CredentialRef: secret,
		},
		Runtime: model.TargetRuntime{RuntimeType: "cpu", Accelerator: "none", OperatingMode: "vm_process"},
	})
	assertAPIError(t, recorder, http.StatusBadRequest, model.ErrTargetProfileInvalid)
	if bytes.Contains(recorder.Body.Bytes(), []byte(secret)) {
		t.Fatal("credential_ref secret was echoed in the error response")
	}

	raw, err := os.ReadFile(storePath)
	if err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte(secret)) {
		t.Fatal("credential_ref secret was persisted in the file store")
	}

	listed := localCredentialRequest(t, e, http.MethodGet, "/api/v1/target-profiles", nil)
	assertStatus(t, listed, http.StatusOK)
	if bytes.Contains(listed.Body.Bytes(), []byte(secret)) {
		t.Fatal("credential_ref secret was exposed by the Target list")
	}
}
