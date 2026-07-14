package server_test

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"strings"
	"testing"

	"github.com/rs/zerolog"
	zerologlog "github.com/rs/zerolog/log"
)

func TestCredentialSecretIsAbsentFromStructuredLogs(t *testing.T) {
	secretBytes := make([]byte, 24)
	if _, err := rand.Read(secretBytes); err != nil {
		t.Fatal(err)
	}
	secret := hex.EncodeToString(secretBytes)

	var logs bytes.Buffer
	previousLogger := zerologlog.Logger
	zerologlog.Logger = zerolog.New(&logs)
	t.Cleanup(func() { zerologlog.Logger = previousLogger })

	e := newCredentialTestServer(t, "", false)
	created := localCredentialRequest(t, e, http.MethodPost, "/api/v1/credentials", map[string]any{
		"credential_id":        "log-redaction-test",
		"credential_type":      "ssh",
		"ssh_user":             "vm-user",
		"auth_type":            "password",
		"password":             secret,
		"host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
	})
	assertStatus(t, created, http.StatusCreated)

	invalid := localCredentialRequest(t, e, http.MethodPost, "/api/v1/credentials", map[string]any{
		"credential_id":        "invalid-log-test",
		"credential_type":      "ssh",
		"ssh_user":             "vm-user",
		"auth_type":            "password",
		"password":             secret,
		"private_key":          secret,
		"host_key_fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
	})
	if invalid.Code != http.StatusBadRequest {
		t.Fatalf("invalid status = %d, want %d", invalid.Code, http.StatusBadRequest)
	}
	if strings.Contains(logs.String(), secret) {
		t.Fatal("credential secret was written to structured logs")
	}
}
