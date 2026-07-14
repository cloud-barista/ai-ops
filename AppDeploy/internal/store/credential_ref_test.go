package store

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFileRejectsStoredSecretCredentialRef(t *testing.T) {
	path := filepath.Join(t.TempDir(), "store.json")
	secret := "stored-secret-canary"
	raw := `{"targets":{"unsafe":{"target_profile_id":"unsafe","csp":"local","vm":{"host":"vm.invalid","credential_ref":"` + secret + `"},"runtime":{"runtime_type":"cpu"}}}}`
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err := NewFile(path)
	if err == nil {
		t.Fatal("NewFile accepted an invalid stored credential_ref")
	}
	if strings.Contains(err.Error(), secret) {
		t.Fatalf("load error exposed stored secret: %v", err)
	}
}
