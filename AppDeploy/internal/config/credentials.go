package config

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/viper"
)

type SSHCredential struct {
	User                 string        `json:"-"`
	PrivateKeyPath       string        `json:"-"`
	PrivateKey           []byte        `json:"-"`
	PrivateKeyPassphrase []byte        `json:"-"`
	Password             string        `json:"-"`
	HostKeyFingerprint   string        `json:"-"`
	Timeout              time.Duration `json:"-"`
}

type SSHCredentialResolver interface {
	ResolveSSH(ctx context.Context, credentialRef string) (SSHCredential, error)
}

type EnvCredentialResolver struct {
	values *viper.Viper
}

func NewEnvCredentialResolver() *EnvCredentialResolver {
	return &EnvCredentialResolver{values: newViper()}
}

func (r *EnvCredentialResolver) ResolveSSH(ctx context.Context, credentialRef string) (SSHCredential, error) {
	select {
	case <-ctx.Done():
		return SSHCredential{}, ctx.Err()
	default:
	}
	if strings.TrimSpace(credentialRef) == "" {
		return SSHCredential{}, fmt.Errorf("credential_ref is empty")
	}
	prefix := "AIAPP_CREDENTIAL_" + NormalizeCredentialRef(credentialRef)
	timeout := 30 * time.Second
	if raw := r.values.GetString(prefix + "_SSH_TIMEOUT"); raw != "" {
		parsed, err := time.ParseDuration(raw)
		if err != nil {
			return SSHCredential{}, fmt.Errorf("invalid ssh timeout for credential_ref")
		}
		timeout = parsed
	}
	credential := SSHCredential{
		User:                 r.values.GetString(prefix + "_SSH_USER"),
		PrivateKeyPath:       r.values.GetString(prefix + "_SSH_KEY_PATH"),
		PrivateKeyPassphrase: []byte(r.values.GetString(prefix + "_SSH_KEY_PASSPHRASE")),
		Password:             r.values.GetString(prefix + "_SSH_PASSWORD"),
		HostKeyFingerprint:   strings.TrimSpace(r.values.GetString(prefix + "_SSH_HOST_KEY_FINGERPRINT")),
		Timeout:              timeout,
	}
	if strings.TrimSpace(credential.User) == "" {
		return SSHCredential{}, fmt.Errorf("ssh user is not configured for credential_ref")
	}
	if strings.TrimSpace(credential.PrivateKeyPath) == "" && strings.TrimSpace(credential.Password) == "" {
		return SSHCredential{}, fmt.Errorf("ssh auth method is not configured for credential_ref")
	}
	if len(credential.PrivateKeyPassphrase) > 0 && strings.TrimSpace(credential.PrivateKeyPath) == "" {
		return SSHCredential{}, fmt.Errorf("ssh private key passphrase requires a key path")
	}
	if credential.HostKeyFingerprint == "" {
		return SSHCredential{}, fmt.Errorf("ssh host key fingerprint is not configured for credential_ref")
	}
	if !IsValidSSHHostKeyFingerprint(credential.HostKeyFingerprint) {
		return SSHCredential{}, fmt.Errorf("ssh host key fingerprint is invalid for credential_ref")
	}
	return credential, nil
}

// IsValidSSHHostKeyFingerprint reports whether value is a canonical OpenSSH
// SHA256 public host key fingerprint.
func IsValidSSHHostKeyFingerprint(value string) bool {
	const prefix = "SHA256:"
	if !strings.HasPrefix(value, prefix) {
		return false
	}
	encoded := strings.TrimPrefix(value, prefix)
	decoded, err := base64.RawStdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) != sha256.Size {
		return false
	}
	return base64.RawStdEncoding.EncodeToString(decoded) == encoded
}

var credentialRefPattern = regexp.MustCompile(`[^A-Za-z0-9]+`)

func NormalizeCredentialRef(credentialRef string) string {
	normalized := strings.ToUpper(credentialRefPattern.ReplaceAllString(credentialRef, "_"))
	normalized = strings.Trim(normalized, "_")
	if normalized == "" {
		return "EMPTY"
	}
	return normalized
}
