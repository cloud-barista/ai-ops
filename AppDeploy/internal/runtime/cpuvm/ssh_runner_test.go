package cpuvm

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/pem"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
	"golang.org/x/crypto/ssh"
)

const testPinnedFingerprint = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

func TestBuildShellCommandPrepareArtifact(t *testing.T) {
	got := buildShellCommand(Command{
		Name: "prepare-artifact",
		Args: []string{"file:///tmp/app's/run.sh", "/opt/aiapp/artifacts/sample"},
	})
	if got == "" || got == "prepare-artifact" {
		t.Fatalf("unexpected shell command: %s", got)
	}
	if want := "mkdir -p '/opt/aiapp/artifacts/sample'"; got[:len(want)] != want {
		t.Fatalf("shell command = %s", got)
	}
}

func TestBuildShellCommandDetachesBackgroundProcess(t *testing.T) {
	got := buildShellCommand(Command{
		Name:       "bash",
		Args:       []string{"run.sh", "--port=18080"},
		WorkingDir: "/opt/aiapp/artifacts/sample",
	})
	if !strings.Contains(got, "setsid -f sh -c") {
		t.Fatalf("shell command does not prefer setsid detach: %s", got)
	}
	if !strings.Contains(got, "echo $$ > .aiapp.pid; exec") {
		t.Fatalf("shell command does not write pid file: %s", got)
	}
	if !strings.Contains(got, "run.sh") || !strings.Contains(got, "--port=18080") {
		t.Fatalf("shell command does not include entrypoint: %s", got)
	}
	if !strings.Contains(got, "(nohup sh -c") {
		t.Fatalf("shell command does not detach stdio: %s", got)
	}
}

func TestBuildShellCommandStopProcess(t *testing.T) {
	got := buildShellCommand(Command{
		Name: "stop-process",
		Args: []string{"/opt/aiapp/artifacts/sample"},
	})
	if !strings.Contains(got, "cd '/opt/aiapp/artifacts/sample'") {
		t.Fatalf("stop command does not cd into working dir: %s", got)
	}
	if !strings.Contains(got, ".aiapp.pid") {
		t.Fatalf("stop command does not use pid file: %s", got)
	}
	if !strings.Contains(got, "/proc/$pid/cwd") || !strings.Contains(got, "safe_pids") {
		t.Fatalf("stop command does not validate candidate pids: %s", got)
	}
	if !strings.Contains(got, "pgrep -f") || !strings.Contains(got, "kill -TERM") {
		t.Fatalf("stop command does not terminate process: %s", got)
	}
}

func TestLocalPathFromFileURI(t *testing.T) {
	localPath := filepath.Join(t.TempDir(), "run script.sh")
	uri := localFileURI(localPath)

	got, ok, err := localPathFromFileURI(uri)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("expected local file uri")
	}
	if filepath.Clean(got) != filepath.Clean(localPath) {
		t.Fatalf("local path = %s, want %s", got, localPath)
	}
}

func TestLocalPathFromNonFileURI(t *testing.T) {
	_, ok, err := localPathFromFileURI("s3://example/app.tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("expected non-file uri")
	}
}

func TestSSHRunnerRequiresCredential(t *testing.T) {
	runner := NewSSHRunner(config.NewEnvCredentialResolver(), 0)
	_, err := runner.Run(context.Background(), model.TargetProfile{
		TargetProfileID: "target-cpu-001",
		VM: model.VMProfile{
			Host:          "127.0.0.1",
			SSHPort:       22,
			CredentialRef: "cred://local/missing",
		},
	}, Command{Name: "uname", Args: []string{"-s"}})
	if err == nil {
		t.Fatal("expected missing credential error")
	}
}

func TestAuthMethodsSupportsRawPrivateKey(t *testing.T) {
	privateKey := testSSHPrivateKey(t, nil)
	methods, err := authMethods(config.SSHCredential{PrivateKey: privateKey})
	if err != nil {
		t.Fatal(err)
	}
	if len(methods) != 1 {
		t.Fatalf("auth method count = %d, want 1", len(methods))
	}
}

func TestAuthMethodsSupportsEncryptedRawPrivateKey(t *testing.T) {
	passphrase := []byte("test-only-passphrase")
	privateKey := testSSHPrivateKey(t, passphrase)
	methods, err := authMethods(config.SSHCredential{
		PrivateKey:           privateKey,
		PrivateKeyPassphrase: append([]byte(nil), passphrase...),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(methods) != 1 {
		t.Fatalf("auth method count = %d, want 1", len(methods))
	}
}

func TestAuthMethodsSupportsEncryptedPrivateKeyPath(t *testing.T) {
	passphrase := []byte("test-only-passphrase")
	privateKey := testSSHPrivateKey(t, passphrase)
	keyPath := filepath.Join(t.TempDir(), "test-key")
	if err := os.WriteFile(keyPath, privateKey, 0o600); err != nil {
		t.Fatal(err)
	}

	methods, err := authMethods(config.SSHCredential{
		PrivateKeyPath:       keyPath,
		PrivateKeyPassphrase: append([]byte(nil), passphrase...),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(methods) != 1 {
		t.Fatalf("auth method count = %d, want 1", len(methods))
	}
}

func TestAuthMethodsDoesNotExposePrivateKeyPassphrase(t *testing.T) {
	privateKey := testSSHPrivateKey(t, []byte("correct-test-passphrase"))
	wrongPassphrase := "sensitive-wrong-test-passphrase"
	_, err := authMethods(config.SSHCredential{
		PrivateKey:           privateKey,
		PrivateKeyPassphrase: []byte(wrongPassphrase),
	})
	if err == nil {
		t.Fatal("expected private key parsing error")
	}
	if strings.Contains(err.Error(), wrongPassphrase) || strings.Contains(err.Error(), string(privateKey)) {
		t.Fatalf("error contains secret material: %v", err)
	}
}

func TestAuthMethodsRejectsAmbiguousPrivateKeySource(t *testing.T) {
	_, err := authMethods(config.SSHCredential{
		PrivateKeyPath: "/test-only/key",
		PrivateKey:     testSSHPrivateKey(t, nil),
	})
	if err == nil {
		t.Fatal("expected ambiguous private key source error")
	}
	if strings.Contains(err.Error(), "/test-only/key") {
		t.Fatalf("error contains private key path: %v", err)
	}
}

func TestPinnedHostKeyCallbackAcceptsOnlyExactFingerprint(t *testing.T) {
	hostKey := testSSHPublicKey(t)
	expected := ssh.FingerprintSHA256(hostKey)
	callback, err := pinnedHostKeyCallback(expected)
	if err != nil {
		t.Fatal(err)
	}
	if err := callback("vm.example.invalid", nil, hostKey); err != nil {
		t.Fatalf("matching host key was rejected: %v", err)
	}

	differentHostKey := testSSHPublicKey(t)
	err = callback("vm.example.invalid", nil, differentHostKey)
	if err == nil {
		t.Fatal("mismatched host key was accepted")
	}
	if strings.Contains(err.Error(), expected) || strings.Contains(err.Error(), ssh.FingerprintSHA256(differentHostKey)) {
		t.Fatalf("host key mismatch error exposed fingerprint values: %v", err)
	}
	if err := callback("vm.example.invalid", nil, nil); err == nil {
		t.Fatal("nil host key was accepted")
	}
}

func TestPinnedHostKeyCallbackRejectsMissingOrInvalidFingerprint(t *testing.T) {
	for _, fingerprint := range []string{"", "SHA256:not-valid", testPinnedFingerprint + "="} {
		callback, err := pinnedHostKeyCallback(fingerprint)
		if err == nil || callback != nil {
			t.Fatalf("fingerprint %q unexpectedly produced a callback", fingerprint)
		}
		if strings.Contains(err.Error(), fingerprint) && fingerprint != "" {
			t.Fatalf("validation error exposed fingerprint value: %v", err)
		}
	}
}

func TestSSHRunnerForwardsCredentialRefAndClearsRawKeyBytes(t *testing.T) {
	privateKey := []byte("sensitive-invalid-test-private-key")
	passphrase := []byte("sensitive-test-passphrase")
	resolver := &capturingResolver{credential: config.SSHCredential{
		User:                 "ubuntu",
		PrivateKey:           privateKey,
		PrivateKeyPassphrase: passphrase,
		HostKeyFingerprint:   testPinnedFingerprint,
	}}
	runner := NewSSHRunner(resolver, 0)
	credentialRef := "cred://runtime/cpu-vm-001"

	_, err := runner.Run(context.Background(), model.TargetProfile{
		TargetProfileID: "target-cpu-001",
		VM: model.VMProfile{
			Host:          "127.0.0.1",
			SSHPort:       22,
			CredentialRef: credentialRef,
		},
	}, Command{Name: "uname", Args: []string{"-s"}})
	if err == nil {
		t.Fatal("expected private key parsing error")
	}
	if resolver.credentialRef != credentialRef {
		t.Fatalf("resolver credential_ref = %q, want %q", resolver.credentialRef, credentialRef)
	}
	for _, secret := range []string{"sensitive-invalid-test-private-key", "sensitive-test-passphrase"} {
		if strings.Contains(err.Error(), secret) {
			t.Fatalf("error contains secret material: %v", err)
		}
	}
	if !zeroed(privateKey) || !zeroed(passphrase) {
		t.Fatal("runner did not clear resolved raw key bytes")
	}
}

func localFileURI(path string) string {
	normalized := filepath.ToSlash(path)
	if strings.HasPrefix(normalized, "/") {
		return (&url.URL{Scheme: "file", Path: normalized}).String()
	}
	return (&url.URL{Scheme: "file", Path: "/" + normalized}).String()
}

type capturingResolver struct {
	credentialRef string
	credential    config.SSHCredential
}

func (r *capturingResolver) ResolveSSH(ctx context.Context, credentialRef string) (config.SSHCredential, error) {
	select {
	case <-ctx.Done():
		return config.SSHCredential{}, ctx.Err()
	default:
	}
	r.credentialRef = credentialRef
	return r.credential, nil
}

func testSSHPrivateKey(t *testing.T, passphrase []byte) []byte {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	var block *pem.Block
	if len(passphrase) > 0 {
		block, err = ssh.MarshalPrivateKeyWithPassphrase(privateKey, "test-only", passphrase)
	} else {
		block, err = ssh.MarshalPrivateKey(privateKey, "test-only")
	}
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(block)
}

func testSSHPublicKey(t *testing.T) ssh.PublicKey {
	t.Helper()
	publicKey, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	hostKey, err := ssh.NewPublicKey(publicKey)
	if err != nil {
		t.Fatal(err)
	}
	return hostKey
}

func zeroed(value []byte) bool {
	for _, b := range value {
		if b != 0 {
			return false
		}
	}
	return true
}
