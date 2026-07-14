package config

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

var testHostKeyFingerprint = "SHA256:" + base64.RawStdEncoding.EncodeToString(make([]byte, sha256.Size))

func TestNormalizeCredentialRef(t *testing.T) {
	got := NormalizeCredentialRef("cred://local/cpu-vm-001")
	if got != "CRED_LOCAL_CPU_VM_001" {
		t.Fatalf("normalized ref = %s", got)
	}
}

func TestEnvCredentialResolver(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_KEY_PATH", "/tmp/key.pem")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_KEY_PASSPHRASE", "test-only-passphrase")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_HOST_KEY_FINGERPRINT", testHostKeyFingerprint)
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_TIMEOUT", "5s")

	credential, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err != nil {
		t.Fatal(err)
	}
	if credential.User != "ubuntu" || credential.PrivateKeyPath != "/tmp/key.pem" {
		t.Fatalf("unexpected credential: %+v", credential)
	}
	if string(credential.PrivateKeyPassphrase) != "test-only-passphrase" {
		t.Fatal("private key passphrase was not loaded")
	}
	if credential.HostKeyFingerprint != testHostKeyFingerprint {
		t.Fatal("host key fingerprint was not loaded")
	}
	if credential.Timeout != 5*time.Second {
		t.Fatalf("timeout = %s", credential.Timeout)
	}
}

func TestEnvCredentialResolverRequiresAuth(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")

	_, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err == nil {
		t.Fatal("expected auth configuration error")
	}
}

func TestEnvCredentialResolverPassword(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_PASSWORD", "test-only-password")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_HOST_KEY_FINGERPRINT", testHostKeyFingerprint)

	credential, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err != nil {
		t.Fatal(err)
	}
	if credential.Password != "test-only-password" {
		t.Fatal("password was not loaded")
	}
}

func TestEnvCredentialResolverRejectsPassphraseWithoutKeyPath(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_PASSWORD", "test-only-password")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_KEY_PASSPHRASE", "test-only-passphrase")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_HOST_KEY_FINGERPRINT", testHostKeyFingerprint)

	_, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err == nil || strings.Contains(err.Error(), "test-only-passphrase") {
		t.Fatalf("unexpected passphrase validation error: %v", err)
	}
}

func TestEnvCredentialResolverRequiresHostKeyFingerprint(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_PASSWORD", "test-only-password")

	_, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err == nil || strings.Contains(err.Error(), "test-only-password") {
		t.Fatalf("unexpected host key fingerprint error: %v", err)
	}
}

func TestEnvCredentialResolverRejectsInvalidHostKeyFingerprint(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_PASSWORD", "test-only-password")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_HOST_KEY_FINGERPRINT", "SHA256:not-valid")

	_, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err == nil || strings.Contains(err.Error(), "SHA256:not-valid") {
		t.Fatalf("unexpected host key fingerprint error: %v", err)
	}
}

func TestIsValidSSHHostKeyFingerprint(t *testing.T) {
	tests := []struct {
		value string
		valid bool
	}{
		{value: testHostKeyFingerprint, valid: true},
		{value: strings.TrimPrefix(testHostKeyFingerprint, "SHA256:"), valid: false},
		{value: testHostKeyFingerprint + "=", valid: false},
		{value: "SHA256:short", valid: false},
		{value: "SHA256:!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", valid: false},
		{value: " " + testHostKeyFingerprint, valid: false},
	}
	for _, test := range tests {
		if got := IsValidSSHHostKeyFingerprint(test.value); got != test.valid {
			t.Fatalf("IsValidSSHHostKeyFingerprint(%q) = %v, want %v", test.value, got, test.valid)
		}
	}
}

func TestEnvCredentialResolverHonorsContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := NewEnvCredentialResolver().ResolveSSH(ctx, "cred://local/cpu-vm-001")
	if err != context.Canceled {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
}

func TestSSHCredentialSecretsAreNotJSONSerialized(t *testing.T) {
	credential := SSHCredential{
		User:                 "test-user",
		PrivateKeyPath:       "/test/key/path",
		PrivateKey:           []byte("test-only-private-key"),
		PrivateKeyPassphrase: []byte("test-only-passphrase"),
		Password:             "test-only-password",
		HostKeyFingerprint:   testHostKeyFingerprint,
		Timeout:              time.Second,
	}

	raw, err := json.Marshal(credential)
	if err != nil {
		t.Fatal(err)
	}
	for _, sensitive := range []string{"test-user", "/test/key/path", "test-only-private-key", "test-only-passphrase", "test-only-password"} {
		if strings.Contains(string(raw), sensitive) {
			t.Fatalf("serialized credential contains sensitive value: %s", raw)
		}
	}
}
