package config

import (
	"context"
	"testing"
	"time"
)

func TestNormalizeCredentialRef(t *testing.T) {
	got := NormalizeCredentialRef("cred://local/cpu-vm-001")
	if got != "CRED_LOCAL_CPU_VM_001" {
		t.Fatalf("normalized ref = %s", got)
	}
}

func TestEnvCredentialResolver(t *testing.T) {
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_USER", "ubuntu")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_KEY_PATH", "/tmp/key.pem")
	t.Setenv("AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_TIMEOUT", "5s")

	credential, err := NewEnvCredentialResolver().ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err != nil {
		t.Fatal(err)
	}
	if credential.User != "ubuntu" || credential.PrivateKeyPath != "/tmp/key.pem" {
		t.Fatalf("unexpected credential: %+v", credential)
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
