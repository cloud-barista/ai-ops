package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"testing"
)

const credentialSecretSentinel = "CREDENTIAL_SECRET_SENTINEL_DO_NOT_PRINT"

var credentialHostKeyFingerprint = "SHA256:" + strings.Repeat("A", 43)

func TestCredentialHelpRequiresHostKeyFingerprint(t *testing.T) {
	var output bytes.Buffer
	if err := New(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}), strings.NewReader(""), &output).Run(context.Background(), []string{"help"}); err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{"host key fingerprint", "--host-key-fingerprint", "SHA256:<base64>"} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("Credential help does not contain %q: %s", expected, output.String())
		}
	}
}

func TestCredentialsListUsesMetadataAllowlistAndLoopbackOrigin(t *testing.T) {
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/api/v1/credentials" {
			t.Fatalf("request = %s %s", r.Method, r.URL.Path)
		}
		if !strings.HasPrefix(r.RemoteAddr, "127.0.0.1:") {
			t.Fatalf("RemoteAddr = %q, want loopback", r.RemoteAddr)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"request_id":"req-1","items":[{"credential_id":"cpu-vm-001","credential_ref":"cred://runtime/cpu-vm-001","credential_type":"ssh","auth_type":"password","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z","password":"` + credentialSecretSentinel + `","private_key":"` + credentialSecretSentinel + `"}]}`))
	})

	if err := New(api, strings.NewReader(""), &output).Run(context.Background(), []string{"credentials", "list"}); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("Credential list printed a write-only Secret")
	}
	for _, expected := range []string{"cpu-vm-001", "cred://runtime/cpu-vm-001", "ubuntu", "password", credentialHostKeyFingerprint} {
		if !strings.Contains(output.String(), expected) {
			t.Fatalf("output does not contain %q: %s", expected, output.String())
		}
	}
}

func TestCredentialsAddReadsPasswordFromStdinWithoutEcho(t *testing.T) {
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/credentials" {
			t.Fatalf("request = %s %s", r.Method, r.URL.Path)
		}
		if !strings.HasPrefix(r.RemoteAddr, "127.0.0.1:") {
			t.Fatalf("RemoteAddr = %q, want loopback", r.RemoteAddr)
		}
		var request credentialCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.CredentialID != "cpu-vm-001" || request.CredentialType != "ssh" || request.AuthType != "password" || request.SSHUser != "ubuntu" || request.HostKeyFingerprint != credentialHostKeyFingerprint {
			t.Fatal("unexpected request metadata")
		}
		if request.Password != credentialSecretSentinel {
			t.Fatal("password stdin was not sent as the write-only password field")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"credential_id":"cpu-vm-001","credential_ref":"cred://runtime/cpu-vm-001","credential_type":"ssh","auth_type":"password","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z","password":"` + credentialSecretSentinel + `"}`))
	})

	shell := New(api, strings.NewReader(credentialSecretSentinel+"\n"), &output)
	err := shell.Run(context.Background(), []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "password", "--password-stdin"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("password was echoed or copied from a faulty API response")
	}
	if !strings.Contains(output.String(), "단발 명령이 끝나면 Credential이 소멸") {
		t.Fatalf("one-shot lifetime warning is missing: %s", output.String())
	}
}

func TestCredentialsAddReadsPrivateKeyFileAsRawPEM(t *testing.T) {
	privateKey := "-----BEGIN PRIVATE KEY-----\nnot-a-real-test-key\n-----END PRIVATE KEY-----\n"
	path := t.TempDir() + string(os.PathSeparator) + "id_test.pem"
	if err := os.WriteFile(path, []byte(privateKey), 0o600); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request credentialCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.PrivateKey != privateKey {
			t.Fatal("private_key was not the raw file body")
		}
		if request.Password != "" || request.PrivateKeyPassphrase != "" {
			t.Fatal("private key request included an unrelated Secret field")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"credential_id":"key-vm-001","credential_ref":"cred://runtime/key-vm-001","credential_type":"ssh","auth_type":"private_key","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z","private_key":"` + credentialSecretSentinel + `"}`))
	})

	err := New(api, strings.NewReader(""), &output).Run(context.Background(), []string{"credentials", "add", "--id", "key-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "private_key", "--private-key-file", path})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), privateKey) || strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("private key material was printed")
	}
}

func TestCredentialsRejectSecretsInArgvAndRequireExplicitNonTTYInput(t *testing.T) {
	called := false
	api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true })

	var output bytes.Buffer
	shell := New(api, strings.NewReader(""), &output)
	err := shell.Run(context.Background(), []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "password", "--password", credentialSecretSentinel})
	if err == nil {
		t.Fatal("inline password argument was accepted")
	}
	if strings.Contains(err.Error(), credentialSecretSentinel) || strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("rejected argv Secret was echoed")
	}
	if called {
		t.Fatal("API was called for a rejected argv Secret")
	}

	output.Reset()
	shell = New(api, strings.NewReader(credentialSecretSentinel+"\n"), &output)
	err = shell.Run(context.Background(), []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "password"})
	if err == nil || !strings.Contains(err.Error(), "TTY") {
		t.Fatalf("non-TTY password without --password-stdin error = %v", err)
	}
	if strings.Contains(output.String(), credentialSecretSentinel) || called {
		t.Fatal("non-TTY password was echoed or sent without explicit stdin authorization")
	}
}

func TestCredentialsRequireValidHostKeyFingerprint(t *testing.T) {
	called := false
	api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true })
	for _, test := range []struct {
		name string
		args []string
	}{
		{
			name: "missing",
			args: []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--auth", "password", "--password-stdin"},
		},
		{
			name: "invalid",
			args: []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", "SHA256:short", "--auth", "password", "--password-stdin"},
		},
		{
			name: "non-canonical-padded",
			args: []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint + "=", "--auth", "password", "--password-stdin"},
		},
		{
			name: "non-canonical-padding-bits",
			args: []string{"credentials", "add", "--id", "cpu-vm-001", "--user", "ubuntu", "--host-key-fingerprint", "SHA256:" + strings.Repeat("A", 42) + "B", "--auth", "password", "--password-stdin"},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			var output bytes.Buffer
			err := New(api, strings.NewReader(credentialSecretSentinel+"\n"), &output).Run(context.Background(), test.args)
			if err == nil || !strings.Contains(err.Error(), "host-key-fingerprint") {
				t.Fatalf("error = %v, want host-key-fingerprint validation", err)
			}
			if strings.Contains(output.String(), credentialSecretSentinel) {
				t.Fatal("password was consumed or printed before fingerprint validation")
			}
		})
	}
	if called {
		t.Fatal("API was called with a missing or invalid host key fingerprint")
	}
}

func TestCredentialsInteractiveModePromptsForHostKeyFingerprint(t *testing.T) {
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request credentialCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.HostKeyFingerprint != credentialHostKeyFingerprint || request.Password != credentialSecretSentinel {
			t.Fatal("prompted fingerprint or hidden password was not sent")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"credential_id":"prompt-vm-001","credential_ref":"cred://runtime/prompt-vm-001","credential_type":"ssh","auth_type":"password","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z"}`))
	})
	shell := New(api, strings.NewReader(credentialHostKeyFingerprint+"\n"), &output)
	shell.interactive = true
	shell.readSecret = func(string) ([]byte, error) { return []byte(credentialSecretSentinel), nil }
	err := shell.credentials(context.Background(), []string{"add", "--id", "prompt-vm-001", "--user", "ubuntu", "--auth", "password"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), "Host key fingerprint") || !strings.Contains(output.String(), "현재 대화형 CLI 프로세스") {
		t.Fatalf("interactive prompt or lifetime warning is missing: %s", output.String())
	}
	if strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("interactive password was printed")
	}
}

func TestCredentialsTTYReaderIsInjectableAndDoesNotPrintSecret(t *testing.T) {
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request credentialCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.Password != credentialSecretSentinel {
			t.Fatal("TTY reader result was not sent")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"credential_id":"tty-vm-001","credential_ref":"cred://runtime/tty-vm-001","credential_type":"ssh","auth_type":"password","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z"}`))
	})
	shell := New(api, strings.NewReader(""), &output)
	shell.readSecret = func(string) ([]byte, error) { return []byte(credentialSecretSentinel), nil }
	if err := shell.Run(context.Background(), []string{"credentials", "add", "--id", "tty-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "password"}); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("TTY Secret was printed")
	}
}

func TestCredentialsEncryptedKeyRequiresExplicitPassphraseStdinForNonTTY(t *testing.T) {
	privateKey := "-----BEGIN ENCRYPTED PRIVATE KEY-----\nnot-a-real-test-key\n-----END ENCRYPTED PRIVATE KEY-----\n"
	path := t.TempDir() + string(os.PathSeparator) + "encrypted.pem"
	if err := os.WriteFile(path, []byte(privateKey), 0o600); err != nil {
		t.Fatal(err)
	}
	called := 0
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		var request credentialCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		if request.PrivateKeyPassphrase != credentialSecretSentinel {
			t.Fatal("explicit passphrase stdin was not sent")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"credential_id":"key-vm-001","credential_ref":"cred://runtime/key-vm-001","credential_type":"ssh","auth_type":"private_key","ssh_user":"ubuntu","host_key_fingerprint":"` + credentialHostKeyFingerprint + `","ssh_timeout_seconds":30,"persistent":false,"created_at":"2026-07-13T00:00:00Z"}`))
	})

	args := []string{"credentials", "add", "--id", "key-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "private_key", "--private-key-file", path}
	err := New(api, strings.NewReader(credentialSecretSentinel+"\n"), &output).Run(context.Background(), args)
	if err == nil || !strings.Contains(err.Error(), "TTY") {
		t.Fatalf("encrypted key without explicit passphrase stdin error = %v", err)
	}
	if called != 0 || strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("passphrase was consumed, sent, or printed without explicit stdin authorization")
	}

	output.Reset()
	args = append(args, "--passphrase-stdin")
	if err := New(api, strings.NewReader(credentialSecretSentinel+"\n"), &output).Run(context.Background(), args); err != nil {
		t.Fatal(err)
	}
	if called != 1 || strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("explicit passphrase stdin was not handled safely")
	}
}

func TestCredentialsDeleteRequiresYesAndUsesDeleteAllowlist(t *testing.T) {
	called := 0
	var output bytes.Buffer
	api := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called++
		if r.Method != http.MethodDelete || r.URL.Path != "/api/v1/credentials/cpu-vm-001" {
			t.Fatalf("request = %s %s", r.Method, r.URL.Path)
		}
		if !strings.HasPrefix(r.RemoteAddr, "127.0.0.1:") {
			t.Fatalf("RemoteAddr = %q, want loopback", r.RemoteAddr)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"credential_id":"cpu-vm-001","credential_ref":"cred://runtime/cpu-vm-001","deleted":true,"deleted_at":"2026-07-13T00:00:00Z","password":"` + credentialSecretSentinel + `"}`))
	})
	shell := New(api, strings.NewReader(""), &output)
	if err := shell.Run(context.Background(), []string{"credentials", "delete", "cpu-vm-001"}); err == nil {
		t.Fatal("delete without --yes was accepted")
	}
	if called != 0 {
		t.Fatal("delete API was called without --yes")
	}
	if err := shell.Run(context.Background(), []string{"credentials", "delete", "cpu-vm-001", "--yes"}); err != nil {
		t.Fatal(err)
	}
	if called != 1 || !strings.Contains(output.String(), `"deleted": true`) {
		t.Fatalf("delete output = %s", output.String())
	}
	if strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("delete output printed a write-only Secret")
	}
}

func TestCredentialsPrivateKeyReadErrorDoesNotExposePath(t *testing.T) {
	path := t.TempDir() + string(os.PathSeparator) + "sensitive-key-name.pem"
	err := New(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}), strings.NewReader(""), &bytes.Buffer{}).Run(
		context.Background(),
		[]string{"credentials", "add", "--id", "key-vm-001", "--user", "ubuntu", "--host-key-fingerprint", credentialHostKeyFingerprint, "--auth", "private_key", "--private-key-file", path},
	)
	if err == nil {
		t.Fatal("missing private key file was accepted")
	}
	if strings.Contains(err.Error(), path) || strings.Contains(err.Error(), "sensitive-key-name") {
		t.Fatalf("private key path leaked in error: %v", err)
	}
}

func TestRawCommandCannotBypassCredentialSecretGuards(t *testing.T) {
	called := false
	var output bytes.Buffer
	api := http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true })
	payload := `{"credential_id":"cpu-vm-001","password":"` + credentialSecretSentinel + `"}`
	err := New(api, strings.NewReader(""), &output).Run(
		context.Background(),
		[]string{"raw", "POST", "/api/v1/credentials", payload},
	)
	if err == nil {
		t.Fatal("raw Credential API request was accepted")
	}
	if called {
		t.Fatal("raw Credential API request reached the handler")
	}
	if strings.Contains(err.Error(), credentialSecretSentinel) || strings.Contains(output.String(), credentialSecretSentinel) {
		t.Fatal("raw Credential payload Secret was echoed")
	}
}
