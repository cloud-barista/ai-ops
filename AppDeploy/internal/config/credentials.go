package config

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/viper"
)

type SSHCredential struct {
	User           string
	PrivateKeyPath string
	Password       string
	Timeout        time.Duration
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
		User:           r.values.GetString(prefix + "_SSH_USER"),
		PrivateKeyPath: r.values.GetString(prefix + "_SSH_KEY_PATH"),
		Password:       r.values.GetString(prefix + "_SSH_PASSWORD"),
		Timeout:        timeout,
	}
	if strings.TrimSpace(credential.User) == "" {
		return SSHCredential{}, fmt.Errorf("ssh user is not configured for credential_ref")
	}
	if strings.TrimSpace(credential.PrivateKeyPath) == "" && strings.TrimSpace(credential.Password) == "" {
		return SSHCredential{}, fmt.Errorf("ssh auth method is not configured for credential_ref")
	}
	return credential, nil
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
