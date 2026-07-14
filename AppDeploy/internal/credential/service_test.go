package credential

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/khu/ai-app-deployer/internal/config"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"golang.org/x/crypto/ssh"
)

const testHostKeyFingerprint = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

func TestServiceCreatesAndResolvesPrivateKeyCredential(t *testing.T) {
	privateKey := testPrivateKey(t, nil)
	fallback := &recordingResolver{}
	service := NewService(fallback, 45*time.Second)

	record, err := service.Create(context.Background(), CreateRequest{
		CredentialID:       "cpu-vm-001",
		CredentialType:     CredentialTypeSSH,
		AuthType:           AuthTypePrivateKey,
		SSHUser:            " ubuntu ",
		HostKeyFingerprint: testHostKeyFingerprint,
		PrivateKey:         stringPointer(privateKey),
	})
	if err != nil {
		t.Fatal(err)
	}
	if record.CredentialRef != "cred://runtime/cpu-vm-001" || record.SSHUser != "ubuntu" || record.HostKeyFingerprint != testHostKeyFingerprint {
		t.Fatalf("unexpected record: %+v", record)
	}
	if record.SSHTimeoutSeconds != 45 || record.Persistent || record.CreatedAt.IsZero() {
		t.Fatalf("unexpected record metadata: %+v", record)
	}

	resolved, err := service.ResolveSSH(context.Background(), record.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if resolved.User != "ubuntu" || string(resolved.PrivateKey) != privateKey || resolved.HostKeyFingerprint != testHostKeyFingerprint || resolved.Timeout != 45*time.Second {
		t.Fatal("resolved credential does not match the registered credential")
	}
	resolved.PrivateKey[0] ^= 0xff

	resolvedAgain, err := service.ResolveSSH(context.Background(), record.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if string(resolvedAgain.PrivateKey) != privateKey {
		t.Fatal("resolve returned vault-owned private key bytes")
	}
	if fallback.callCount() != 0 {
		t.Fatal("runtime credential unexpectedly used fallback resolver")
	}

	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), privateKey) || strings.Contains(string(raw), `"private_key":`) {
		t.Fatalf("record contains private key material: %s", raw)
	}
}

func TestServiceCreatesEncryptedPrivateKeyCredential(t *testing.T) {
	passphrase := "test-only-passphrase"
	privateKey := testPrivateKey(t, []byte(passphrase))
	service := NewService(nil, 30*time.Second)

	record, err := service.Create(context.Background(), CreateRequest{
		CredentialID:         "gpu-vm-001",
		CredentialType:       CredentialTypeSSH,
		AuthType:             AuthTypePrivateKey,
		SSHUser:              "ubuntu",
		HostKeyFingerprint:   testHostKeyFingerprint,
		PrivateKey:           stringPointer(privateKey),
		PrivateKeyPassphrase: stringPointer(passphrase),
	})
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := service.ResolveSSH(context.Background(), record.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ssh.ParsePrivateKeyWithPassphrase(resolved.PrivateKey, resolved.PrivateKeyPassphrase); err != nil {
		t.Fatal("resolved encrypted private key could not be parsed")
	}
	resolved.PrivateKeyPassphrase[0] ^= 0xff
	resolvedAgain, err := service.ResolveSSH(context.Background(), record.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if string(resolvedAgain.PrivateKeyPassphrase) != passphrase {
		t.Fatal("resolve returned vault-owned passphrase bytes")
	}

	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), passphrase) || strings.Contains(string(raw), privateKey) {
		t.Fatalf("record contains secret material: %s", raw)
	}
}

func TestServicePasswordCredentialAndFallbackRouting(t *testing.T) {
	fallbackPrivateKey := []byte("test-only-fallback-private-key")
	fallbackPassphrase := []byte("test-only-fallback-passphrase")
	fallback := &recordingResolver{credential: config.SSHCredential{
		User:                 "fallback-user",
		PrivateKey:           fallbackPrivateKey,
		PrivateKeyPassphrase: fallbackPassphrase,
		Password:             "test-only-fallback-password",
		HostKeyFingerprint:   testHostKeyFingerprint,
		Timeout:              10 * time.Second,
	}}
	service := NewService(fallback, 30*time.Second)

	record, err := service.Create(context.Background(), validPasswordRequest("password-credential"))
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := service.ResolveSSH(context.Background(), record.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if resolved.Password != "test-only-password" {
		t.Fatal("runtime password credential was not resolved")
	}

	fallbackCredential, err := service.ResolveSSH(context.Background(), "cred://local/cpu-vm-001")
	if err != nil {
		t.Fatal(err)
	}
	if fallbackCredential.User != "fallback-user" || fallback.callCount() != 1 {
		t.Fatal("non-runtime credential did not use fallback resolver")
	}
	fallbackCredential.PrivateKey[0] ^= 0xff
	fallbackCredential.PrivateKeyPassphrase[0] ^= 0xff
	if string(fallbackPrivateKey) != "test-only-fallback-private-key" || string(fallbackPassphrase) != "test-only-fallback-passphrase" {
		t.Fatal("resolve returned fallback-owned secret bytes")
	}

	for _, ref := range []string{
		"cred://runtime/missing-credential",
		"cred://runtime/Bad-ID",
		"cred://runtime",
		"CRED://RUNTIME/password-credential",
	} {
		if _, err := service.ResolveSSH(context.Background(), ref); err == nil {
			t.Fatalf("ResolveSSH(%q) unexpectedly succeeded", ref)
		}
	}
	if fallback.callCount() != 1 {
		t.Fatal("runtime namespace failure unexpectedly used fallback resolver")
	}
}

func TestServiceRejectsInvalidCreateRequests(t *testing.T) {
	validKey := testPrivateKey(t, nil)
	encryptedKey := testPrivateKey(t, []byte("correct-test-passphrase"))
	tests := []struct {
		name string
		req  CreateRequest
	}{
		{name: "short id", req: withPasswordRequest("a")},
		{name: "invalid id", req: withPasswordRequest("Bad-ID")},
		{name: "credential type", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.CredentialType = "token"; return r }()},
		{name: "missing user", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.SSHUser = " "; return r }()},
		{name: "user control character", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.SSHUser = "ubuntu\nroot"; return r }()},
		{name: "user trailing tab", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.SSHUser = "ubuntu\t"; return r }()},
		{name: "long user", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.SSHUser = strings.Repeat("u", MaxSSHUserLength+1)
			return r
		}()},
		{name: "missing host key fingerprint", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.HostKeyFingerprint = ""
			return r
		}()},
		{name: "invalid host key fingerprint", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.HostKeyFingerprint = "SHA256:not-valid"
			return r
		}()},
		{name: "auth type", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.AuthType = "agent"; return r }()},
		{name: "missing password", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.Password = nil; return r }()},
		{name: "empty password", req: func() CreateRequest { r := validPasswordRequest("valid-id"); r.Password = stringPointer(""); return r }()},
		{name: "long password", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.Password = stringPointer(strings.Repeat("p", MaxPasswordRunes+1))
			return r
		}()},
		{name: "password with private key", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.PrivateKey = stringPointer(validKey)
			return r
		}()},
		{name: "password with empty private key", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.PrivateKey = stringPointer("")
			return r
		}()},
		{name: "password with passphrase", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.PrivateKeyPassphrase = stringPointer("test-only-passphrase")
			return r
		}()},
		{name: "password with empty passphrase", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.PrivateKeyPassphrase = stringPointer("")
			return r
		}()},
		{name: "missing private key", req: func() CreateRequest {
			r := privateKeyRequest("valid-id", "placeholder", "")
			r.PrivateKey = nil
			return r
		}()},
		{name: "empty private key", req: privateKeyRequest("valid-id", "", "")},
		{name: "invalid private key", req: privateKeyRequest("valid-id", "sensitive-invalid-private-key", "")},
		{name: "wrong private key passphrase", req: privateKeyRequest("valid-id", encryptedKey, "sensitive-wrong-test-passphrase")},
		{name: "long private key", req: privateKeyRequest("valid-id", strings.Repeat("k", MaxPrivateKeyBytes+1), "")},
		{name: "empty passphrase", req: func() CreateRequest {
			r := privateKeyRequest("valid-id", validKey, "")
			r.PrivateKeyPassphrase = stringPointer("")
			return r
		}()},
		{name: "long passphrase", req: privateKeyRequest("valid-id", validKey, strings.Repeat("p", MaxPassphraseRunes+1))},
		{name: "private key with password", req: func() CreateRequest {
			r := privateKeyRequest("valid-id", validKey, "")
			r.Password = stringPointer("test-only-password")
			return r
		}()},
		{name: "private key with empty password", req: func() CreateRequest {
			r := privateKeyRequest("valid-id", validKey, "")
			r.Password = stringPointer("")
			return r
		}()},
		{name: "timeout zero", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.SSHTimeoutSeconds = intPointer(0)
			return r
		}()},
		{name: "timeout below minimum", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.SSHTimeoutSeconds = intPointer(-1)
			return r
		}()},
		{name: "timeout above maximum", req: func() CreateRequest {
			r := validPasswordRequest("valid-id")
			r.SSHTimeoutSeconds = intPointer(MaxSSHTimeoutSeconds + 1)
			return r
		}()},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			service := NewService(nil, 30*time.Second)
			_, err := service.Create(context.Background(), test.req)
			assertAppError(t, err, model.ErrTargetProfileInvalid, http.StatusBadRequest)
			for _, secret := range []string{"sensitive-invalid-private-key", "sensitive-wrong-test-passphrase", "test-only-password", "test-only-passphrase"} {
				if strings.Contains(err.Error(), secret) {
					t.Fatalf("error contains secret material: %v", err)
				}
			}
		})
	}
}

func TestServiceSecretRuneLengthBoundaries(t *testing.T) {
	service := NewService(nil, 30*time.Second)
	password := strings.Repeat("가", MaxPasswordRunes)
	passwordRequest := validPasswordRequest("password-rune-boundary")
	passwordRequest.Password = stringPointer(password)
	passwordRecord, err := service.Create(context.Background(), passwordRequest)
	if err != nil {
		t.Fatal(err)
	}
	resolvedPassword, err := service.ResolveSSH(context.Background(), passwordRecord.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if resolvedPassword.Password != password {
		t.Fatal("password at rune boundary was not preserved")
	}

	passphrase := strings.Repeat("나", MaxPassphraseRunes)
	privateKey := testPrivateKey(t, []byte(passphrase))
	keyRecord, err := service.Create(context.Background(), privateKeyRequest("passphrase-rune-boundary", privateKey, passphrase))
	if err != nil {
		t.Fatal(err)
	}
	resolvedKey, err := service.ResolveSSH(context.Background(), keyRecord.CredentialRef)
	if err != nil {
		t.Fatal(err)
	}
	if string(resolvedKey.PrivateKeyPassphrase) != passphrase {
		t.Fatal("passphrase at rune boundary was not preserved")
	}

	overPassword := validPasswordRequest("password-over-boundary")
	overPassword.Password = stringPointer(strings.Repeat("가", MaxPasswordRunes+1))
	_, err = service.Create(context.Background(), overPassword)
	assertAppError(t, err, model.ErrTargetProfileInvalid, http.StatusBadRequest)

	overPassphrase := privateKeyRequest("passphrase-over-boundary", privateKey, "")
	overPassphrase.PrivateKeyPassphrase = stringPointer(strings.Repeat("나", MaxPassphraseRunes+1))
	_, err = service.Create(context.Background(), overPassphrase)
	assertAppError(t, err, model.ErrTargetProfileInvalid, http.StatusBadRequest)
}

func TestServiceRejectsDuplicateCredentialID(t *testing.T) {
	service := NewService(nil, 30*time.Second)
	if _, err := service.Create(context.Background(), validPasswordRequest("duplicate-id")); err != nil {
		t.Fatal(err)
	}

	duplicate := validPasswordRequest("duplicate-id")
	duplicate.Password = stringPointer("test-only-replacement-password")
	_, err := service.Create(context.Background(), duplicate)
	assertAppError(t, err, model.ErrTargetProfileInvalid, http.StatusConflict)

	resolved, err := service.ResolveSSH(context.Background(), RuntimeCredentialPrefix+"duplicate-id")
	if err != nil {
		t.Fatal(err)
	}
	if resolved.Password != "test-only-password" {
		t.Fatal("duplicate create replaced the existing credential")
	}
}

func TestServiceDeleteZerosStoredSecrets(t *testing.T) {
	passphrase := "test-only-passphrase"
	service := NewService(&recordingResolver{}, 30*time.Second)
	if _, err := service.Create(context.Background(), CreateRequest{
		CredentialID:         "private-key-id",
		CredentialType:       CredentialTypeSSH,
		AuthType:             AuthTypePrivateKey,
		SSHUser:              "ubuntu",
		HostKeyFingerprint:   testHostKeyFingerprint,
		PrivateKey:           stringPointer(testPrivateKey(t, []byte(passphrase))),
		PrivateKeyPassphrase: stringPointer(passphrase),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.Create(context.Background(), validPasswordRequest("password-id")); err != nil {
		t.Fatal(err)
	}

	service.mu.RLock()
	privateKeyBytes := service.entries["private-key-id"].privateKey
	passphraseBytes := service.entries["private-key-id"].privateKeyPassphrase
	passwordBytes := service.entries["password-id"].password
	service.mu.RUnlock()

	for _, credentialID := range []string{"private-key-id", "password-id"} {
		resp, err := service.Delete(context.Background(), credentialID)
		if err != nil {
			t.Fatal(err)
		}
		if !resp.Deleted || resp.CredentialID != credentialID || resp.CredentialRef != RuntimeCredentialPrefix+credentialID || resp.DeletedAt.IsZero() {
			t.Fatalf("unexpected delete response: %+v", resp)
		}
	}
	for name, value := range map[string][]byte{
		"private key": privateKeyBytes,
		"passphrase":  passphraseBytes,
		"password":    passwordBytes,
	} {
		if !allZero(value) {
			t.Fatalf("stored %s bytes were not zeroed", name)
		}
	}

	if _, err := service.ResolveSSH(context.Background(), RuntimeCredentialPrefix+"private-key-id"); err == nil {
		t.Fatal("deleted credential was resolved")
	}
	_, err := service.Delete(context.Background(), "private-key-id")
	assertAppError(t, err, "NOT_FOUND", http.StatusNotFound)
}

func TestServiceListIsSortedAndContextAware(t *testing.T) {
	service := NewService(nil, 30*time.Second)
	for _, credentialID := range []string{"zz-credential", "aa-credential"} {
		if _, err := service.Create(context.Background(), validPasswordRequest(credentialID)); err != nil {
			t.Fatal(err)
		}
	}
	items, err := service.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 || items[0].CredentialID != "aa-credential" || items[1].CredentialID != "zz-credential" {
		t.Fatalf("unexpected list order: %+v", items)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := service.Create(ctx, validPasswordRequest("canceled-create")); !errors.Is(err, context.Canceled) {
		t.Fatalf("create error = %v, want context.Canceled", err)
	}
	if _, err := service.List(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("list error = %v, want context.Canceled", err)
	}
	if _, err := service.Delete(ctx, "aa-credential"); !errors.Is(err, context.Canceled) {
		t.Fatalf("delete error = %v, want context.Canceled", err)
	}
	if _, err := service.ResolveSSH(ctx, RuntimeCredentialPrefix+"aa-credential"); !errors.Is(err, context.Canceled) {
		t.Fatalf("resolve error = %v, want context.Canceled", err)
	}
}

func TestServiceDefaultTimeoutNormalization(t *testing.T) {
	tests := []struct {
		name           string
		defaultTimeout time.Duration
		wantSeconds    int
	}{
		{name: "zero", defaultTimeout: 0, wantSeconds: 30},
		{name: "configured", defaultTimeout: 15 * time.Second, wantSeconds: 15},
		{name: "fraction rounds up", defaultTimeout: 1500 * time.Millisecond, wantSeconds: 2},
		{name: "too large", defaultTimeout: 301 * time.Second, wantSeconds: 30},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			service := NewService(nil, test.defaultTimeout)
			record, err := service.Create(context.Background(), validPasswordRequest("timeout-id"))
			if err != nil {
				t.Fatal(err)
			}
			if record.SSHTimeoutSeconds != test.wantSeconds {
				t.Fatalf("timeout = %d, want %d", record.SSHTimeoutSeconds, test.wantSeconds)
			}
		})
	}
}

func TestServiceConcurrentCRUDAndResolve(t *testing.T) {
	service := NewService(&recordingResolver{}, 30*time.Second)
	const workers = 32
	errs := make(chan error, workers)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			credentialID := fmt.Sprintf("worker-%02d", index)
			record, err := service.Create(context.Background(), validPasswordRequest(credentialID))
			if err != nil {
				errs <- err
				return
			}
			if _, err := service.ResolveSSH(context.Background(), record.CredentialRef); err != nil {
				errs <- err
				return
			}
			if _, err := service.List(context.Background()); err != nil {
				errs <- err
				return
			}
			if _, err := service.Delete(context.Background(), credentialID); err != nil {
				errs <- err
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	items, err := service.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 0 {
		t.Fatalf("credentials remain after concurrent deletes: %d", len(items))
	}
}

type recordingResolver struct {
	mu         sync.Mutex
	calls      int
	credential config.SSHCredential
	err        error
}

func (r *recordingResolver) ResolveSSH(ctx context.Context, credentialRef string) (config.SSHCredential, error) {
	if err := contextError(ctx); err != nil {
		return config.SSHCredential{}, err
	}
	r.mu.Lock()
	r.calls++
	credential := r.credential
	err := r.err
	r.mu.Unlock()
	return credential, err
}

func (r *recordingResolver) callCount() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.calls
}

func validPasswordRequest(credentialID string) CreateRequest {
	return CreateRequest{
		CredentialID:       credentialID,
		CredentialType:     CredentialTypeSSH,
		AuthType:           AuthTypePassword,
		SSHUser:            "ubuntu",
		HostKeyFingerprint: testHostKeyFingerprint,
		Password:           stringPointer("test-only-password"),
		SSHTimeoutSeconds:  nil,
	}
}

func withPasswordRequest(credentialID string) CreateRequest {
	return validPasswordRequest(credentialID)
}

func privateKeyRequest(credentialID, privateKey, passphrase string) CreateRequest {
	req := CreateRequest{
		CredentialID:       credentialID,
		CredentialType:     CredentialTypeSSH,
		AuthType:           AuthTypePrivateKey,
		SSHUser:            "ubuntu",
		HostKeyFingerprint: testHostKeyFingerprint,
		PrivateKey:         stringPointer(privateKey),
	}
	if passphrase != "" {
		req.PrivateKeyPassphrase = stringPointer(passphrase)
	}
	return req
}

func stringPointer(value string) *string {
	return &value
}

func intPointer(value int) *int {
	return &value
}

func testPrivateKey(t *testing.T, passphrase []byte) string {
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
	return string(pem.EncodeToMemory(block))
}

func assertAppError(t *testing.T, err error, code string, status int) {
	t.Helper()
	if err == nil {
		t.Fatal("expected error")
	}
	var appErr *apperrors.AppError
	if !errors.As(err, &appErr) {
		t.Fatalf("error type = %T, want *errors.AppError: %v", err, err)
	}
	if appErr.Code != code || appErr.HTTPStatus != status {
		t.Fatalf("error = %+v, want code=%s status=%d", appErr, code, status)
	}
}

func allZero(value []byte) bool {
	for _, b := range value {
		if b != 0 {
			return false
		}
	}
	return true
}
