package server_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
	"github.com/khu/ai-app-deployer/internal/server"
)

type credentialAPIRecord struct {
	RequestID          string    `json:"request_id"`
	CredentialID       string    `json:"credential_id"`
	CredentialRef      string    `json:"credential_ref"`
	CredentialType     string    `json:"credential_type"`
	SSHUser            string    `json:"ssh_user"`
	AuthType           string    `json:"auth_type"`
	HostKeyFingerprint string    `json:"host_key_fingerprint"`
	SSHTimeoutSeconds  int       `json:"ssh_timeout_seconds"`
	Persistent         bool      `json:"persistent"`
	CreatedAt          time.Time `json:"created_at"`
}

func TestCredentialLifecycleIsRedactedAndMemoryOnly(t *testing.T) {
	storePath := filepath.Join(t.TempDir(), "store.json")
	e := newCredentialTestServer(t, storePath, false)
	secret := "credential-canary-must-never-return"
	hostKeyFingerprint := "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

	createdRecorder := localCredentialRequest(t, e, http.MethodPost, "/api/v1/credentials", map[string]any{
		"credential_id":        "nhn-vm-001",
		"credential_type":      "ssh",
		"ssh_user":             "vm-user",
		"auth_type":            "password",
		"password":             secret,
		"host_key_fingerprint": hostKeyFingerprint,
		"ssh_timeout_seconds":  45,
	})
	assertStatus(t, createdRecorder, http.StatusCreated)
	assertCredentialSecretAbsent(t, createdRecorder.Body.Bytes(), secret)
	var created credentialAPIRecord
	decodeRecorder(t, createdRecorder, &created)
	if created.RequestID == "" || created.CredentialID != "nhn-vm-001" || created.CredentialRef != "cred://runtime/nhn-vm-001" {
		t.Fatalf("unexpected create response: %+v", created)
	}
	if created.CredentialType != "ssh" || created.SSHUser != "vm-user" || created.AuthType != "password" || created.HostKeyFingerprint != hostKeyFingerprint || created.SSHTimeoutSeconds != 45 || created.Persistent {
		t.Fatalf("unexpected safe metadata: %+v", created)
	}

	listedRecorder := localCredentialRequest(t, e, http.MethodGet, "/api/v1/credentials", nil)
	assertStatus(t, listedRecorder, http.StatusOK)
	if listedRecorder.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("credential list cache control = %q", listedRecorder.Header().Get("Cache-Control"))
	}
	assertCredentialSecretAbsent(t, listedRecorder.Body.Bytes(), secret)
	var listed struct {
		RequestID string                `json:"request_id"`
		Items     []credentialAPIRecord `json:"items"`
	}
	decodeRecorder(t, listedRecorder, &listed)
	if listed.RequestID == "" || len(listed.Items) != 1 || listed.Items[0].CredentialRef != created.CredentialRef {
		t.Fatalf("unexpected list response: %+v", listed)
	}

	if raw, err := os.ReadFile(storePath); err == nil && bytes.Contains(raw, []byte(secret)) {
		t.Fatal("credential secret was persisted in the file store")
	}

	targetRecorder := localCredentialRequest(t, e, http.MethodPost, "/api/v1/target-profiles", model.TargetProfile{
		TargetProfileID: "target-nhn-vm-001",
		Name:            "nhn-vm-target",
		CSP:             "local",
		VM: model.VMProfile{
			Host:          "vm.example.invalid",
			SSHPort:       22,
			CredentialRef: created.CredentialRef,
		},
		Runtime: model.TargetRuntime{RuntimeType: "cpu", Accelerator: "none", OperatingMode: "vm_process"},
	})
	assertStatus(t, targetRecorder, http.StatusCreated)
	if raw, err := os.ReadFile(storePath); err != nil {
		t.Fatal(err)
	} else if bytes.Contains(raw, []byte(secret)) {
		t.Fatal("credential secret was persisted while storing the target reference")
	}

	deletedRecorder := localCredentialRequest(t, e, http.MethodDelete, "/api/v1/credentials/nhn-vm-001", nil)
	assertStatus(t, deletedRecorder, http.StatusOK)
	assertCredentialSecretAbsent(t, deletedRecorder.Body.Bytes(), secret)
	var deleted struct {
		CredentialID string `json:"credential_id"`
		Deleted      bool   `json:"deleted"`
	}
	decodeRecorder(t, deletedRecorder, &deleted)
	if deleted.CredentialID != created.CredentialID || !deleted.Deleted {
		t.Fatalf("unexpected delete response: %+v", deleted)
	}

	missingRecorder := localCredentialRequest(t, e, http.MethodDelete, "/api/v1/credentials/nhn-vm-001", nil)
	assertAPIError(t, missingRecorder, http.StatusNotFound, "NOT_FOUND")

	restarted := newCredentialTestServer(t, storePath, false)
	restartedList := localCredentialRequest(t, restarted, http.MethodGet, "/api/v1/credentials", nil)
	assertStatus(t, restartedList, http.StatusOK)
	var empty struct {
		Items []credentialAPIRecord `json:"items"`
	}
	decodeRecorder(t, restartedList, &empty)
	if len(empty.Items) != 0 {
		t.Fatalf("credentials survived a server restart: %+v", empty.Items)
	}
	targetsRecorder := localCredentialRequest(t, restarted, http.MethodGet, "/api/v1/target-profiles", nil)
	assertStatus(t, targetsRecorder, http.StatusOK)
	var targets struct {
		Items []model.TargetProfile `json:"items"`
	}
	decodeRecorder(t, targetsRecorder, &targets)
	if len(targets.Items) != 1 || targets.Items[0].VM.CredentialRef != created.CredentialRef {
		t.Fatalf("target credential_ref was not retained after credential deletion/restart: %+v", targets.Items)
	}
}

func TestCredentialAPIRejectsRemoteRequestsByDefault(t *testing.T) {
	e := newCredentialTestServer(t, "", false)
	recorder := credentialRequest(t, e, http.MethodGet, "/api/v1/credentials", nil, "198.51.100.7:4242")
	assertAPIError(t, recorder, http.StatusForbidden, model.ErrGatewayAuthFailed)

	remoteEnabled := newCredentialTestServer(t, "", true)
	allowed := credentialRequest(t, remoteEnabled, http.MethodGet, "/api/v1/credentials", nil, "198.51.100.7:4242")
	assertStatus(t, allowed, http.StatusOK)
}

func TestCredentialAPIRejectsProxyRebindingCrossOriginAndNonJSON(t *testing.T) {
	e := newCredentialTestServer(t, "", false)

	proxyRequest := httptest.NewRequest(http.MethodGet, "/api/v1/credentials", nil)
	proxyRequest.RemoteAddr = "127.0.0.1:4242"
	proxyRequest.Host = "credentials.example.com"
	proxyRequest.Header.Set("X-Request-ID", "req-test-001")
	proxyRecorder := httptest.NewRecorder()
	e.ServeHTTP(proxyRecorder, proxyRequest)
	assertAPIError(t, proxyRecorder, http.StatusForbidden, model.ErrGatewayAuthFailed)

	crossOriginRequest := httptest.NewRequest(http.MethodGet, "/api/v1/credentials", nil)
	crossOriginRequest.RemoteAddr = "127.0.0.1:4242"
	crossOriginRequest.Host = "127.0.0.1:8080"
	crossOriginRequest.Header.Set("Origin", "https://attacker.example")
	crossOriginRequest.Header.Set("X-Request-ID", "req-test-001")
	crossOriginRecorder := httptest.NewRecorder()
	e.ServeHTTP(crossOriginRecorder, crossOriginRequest)
	assertAPIError(t, crossOriginRecorder, http.StatusForbidden, model.ErrGatewayAuthFailed)

	nonJSONRequest := httptest.NewRequest(http.MethodPost, "/api/v1/credentials", strings.NewReader("not-json"))
	nonJSONRequest.RemoteAddr = "127.0.0.1:4242"
	nonJSONRequest.Host = "127.0.0.1:8080"
	nonJSONRequest.Header.Set("Content-Type", "text/plain")
	nonJSONRequest.Header.Set("X-Request-ID", "req-test-001")
	nonJSONRecorder := httptest.NewRecorder()
	e.ServeHTTP(nonJSONRecorder, nonJSONRequest)
	assertAPIError(t, nonJSONRecorder, http.StatusBadRequest, model.ErrTargetProfileInvalid)
}

func TestCredentialAPIRejectsUnsafeRequestsWithoutEchoingSecrets(t *testing.T) {
	e := newCredentialTestServer(t, "", false)
	secret := "unsafe-request-canary"

	unknownField := localCredentialRawRequest(t, e, http.MethodPost, "/api/v1/credentials", []byte(`{
  "credential_id":"nhn-vm-001",
  "credential_type":"ssh",
  "ssh_user":"vm-user",
  "auth_type":"password",
  "password":"`+secret+`",
  "unexpected":"`+secret+`"
}`))
	assertAPIError(t, unknownField, http.StatusBadRequest, model.ErrTargetProfileInvalid)
	assertCredentialSecretAbsent(t, unknownField.Body.Bytes(), secret)

	tooLarge := localCredentialRequest(t, e, http.MethodPost, "/api/v1/credentials", map[string]any{
		"credential_id":   "nhn-vm-001",
		"credential_type": "ssh",
		"ssh_user":        "vm-user",
		"auth_type":       "password",
		"password":        strings.Repeat("x", 140<<10),
	})
	assertAPIError(t, tooLarge, http.StatusRequestEntityTooLarge, model.ErrTargetProfileInvalid)
}

func newCredentialTestServer(t *testing.T, storePath string, allowRemote bool) http.Handler {
	t.Helper()
	e, err := server.NewWithConfig(config.Settings{
		StorePath:         storePath,
		CPUVMRunner:       "dry-run",
		GPUVMRunner:       "dry-run",
		SSHDefaultTimeout: 30 * time.Second,
		CredentialRemote:  allowRemote,
		PackageOutputDir:  filepath.Join(t.TempDir(), "packages"),
	})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func localCredentialRequest(t *testing.T, handler http.Handler, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	return credentialRequest(t, handler, method, path, body, "127.0.0.1:4242")
}

func credentialRequest(t *testing.T, handler http.Handler, method, path string, body any, remoteAddress string) *httptest.ResponseRecorder {
	t.Helper()
	var raw []byte
	if body != nil {
		var err error
		raw, err = json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
	}
	return credentialRawRequest(t, handler, method, path, raw, remoteAddress)
}

func localCredentialRawRequest(t *testing.T, handler http.Handler, method, path string, raw []byte) *httptest.ResponseRecorder {
	t.Helper()
	return credentialRawRequest(t, handler, method, path, raw, "[::1]:4242")
}

func credentialRawRequest(t *testing.T, handler http.Handler, method, path string, raw []byte, remoteAddress string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewReader(raw))
	req.RemoteAddr = remoteAddress
	req.Host = "127.0.0.1"
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-ID", "req-test-001")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	return recorder
}

func assertCredentialSecretAbsent(t *testing.T, raw []byte, secret string) {
	t.Helper()
	if bytes.Contains(raw, []byte(secret)) {
		t.Fatalf("credential secret leaked in response: %s", raw)
	}
	for _, field := range []string{`"password":`, `"private_key":`, `"private_key_passphrase":`} {
		if bytes.Contains(raw, []byte(field)) {
			t.Fatalf("credential secret field %s leaked in response: %s", field, raw)
		}
	}
}

func assertStatus(t *testing.T, recorder *httptest.ResponseRecorder, want int) {
	t.Helper()
	if recorder.Code != want {
		t.Fatalf("status = %d, want %d body=%s", recorder.Code, want, recorder.Body.String())
	}
}

func decodeRecorder(t *testing.T, recorder *httptest.ResponseRecorder, target any) {
	t.Helper()
	if err := json.Unmarshal(recorder.Body.Bytes(), target); err != nil {
		t.Fatalf("decode response: %v body=%s", err, recorder.Body.String())
	}
}
