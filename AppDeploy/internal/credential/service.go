package credential

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/khu/ai-app-deployer/internal/config"
	apperrors "github.com/khu/ai-app-deployer/internal/errors"
	"github.com/khu/ai-app-deployer/internal/model"
	"golang.org/x/crypto/ssh"
)

const (
	CredentialTypeSSH       = "ssh"
	AuthTypePrivateKey      = "private_key"
	AuthTypePassword        = "password"
	RuntimeCredentialPrefix = "cred://runtime/"

	MaxCredentialIDLength = 63
	MaxSSHUserLength      = 128
	MaxPrivateKeyBytes    = 65536
	MaxPassphraseRunes    = 4096
	MaxPasswordRunes      = 4096
	MaxSSHTimeoutSeconds  = 300
)

const defaultSSHTimeoutSeconds = 30

var credentialIDPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// CreateRequest contains an SSH credential registration request. Secret fields
// are accepted only as input and are never copied into Record values.
type CreateRequest struct {
	CredentialID         string  `json:"credential_id"`
	CredentialType       string  `json:"credential_type"`
	AuthType             string  `json:"auth_type"`
	SSHUser              string  `json:"ssh_user"`
	HostKeyFingerprint   string  `json:"host_key_fingerprint"`
	PrivateKey           *string `json:"private_key,omitempty"`
	PrivateKeyPassphrase *string `json:"private_key_passphrase,omitempty"`
	Password             *string `json:"password,omitempty"`
	SSHTimeoutSeconds    *int    `json:"ssh_timeout_seconds,omitempty"`
}

// Record is safe credential metadata. It intentionally contains no secret or
// server-local key path.
type Record struct {
	CredentialID       string    `json:"credential_id"`
	CredentialRef      string    `json:"credential_ref"`
	CredentialType     string    `json:"credential_type"`
	AuthType           string    `json:"auth_type"`
	SSHUser            string    `json:"ssh_user"`
	HostKeyFingerprint string    `json:"host_key_fingerprint"`
	SSHTimeoutSeconds  int       `json:"ssh_timeout_seconds"`
	Persistent         bool      `json:"persistent"`
	CreatedAt          time.Time `json:"created_at"`
}

// DeleteResponse reports deletion without returning deleted secret material.
type DeleteResponse struct {
	CredentialID  string    `json:"credential_id"`
	CredentialRef string    `json:"credential_ref"`
	Deleted       bool      `json:"deleted"`
	DeletedAt     time.Time `json:"deleted_at"`
}

// Service owns process-memory-only credentials and resolves runtime credential
// references. References outside the runtime namespace use the existing
// fallback resolver.
type Service struct {
	mu                    sync.RWMutex
	entries               map[string]*entry
	fallback              config.SSHCredentialResolver
	defaultTimeoutSeconds int
}

type entry struct {
	record               Record
	privateKey           []byte
	privateKeyPassphrase []byte
	password             []byte
}

func NewService(fallback config.SSHCredentialResolver, defaultTimeout time.Duration) *Service {
	return &Service{
		entries:               make(map[string]*entry),
		fallback:              fallback,
		defaultTimeoutSeconds: normalizeDefaultTimeout(defaultTimeout),
	}
}

func (s *Service) Create(ctx context.Context, req CreateRequest) (Record, error) {
	if err := contextError(ctx); err != nil {
		return Record{}, err
	}

	candidate, err := newEntry(req, s.defaultTimeout())
	if err != nil {
		return Record{}, err
	}
	if err := contextError(ctx); err != nil {
		zeroEntry(candidate)
		return Record{}, err
	}

	s.mu.Lock()
	if s.entries == nil {
		s.entries = make(map[string]*entry)
	}
	if _, exists := s.entries[candidate.record.CredentialID]; exists {
		s.mu.Unlock()
		zeroEntry(candidate)
		return Record{}, apperrors.New(model.ErrTargetProfileInvalid, "credential_id already exists", http.StatusConflict, false)
	}
	s.entries[candidate.record.CredentialID] = candidate
	s.mu.Unlock()

	return candidate.record, nil
}

func (s *Service) List(ctx context.Context) ([]Record, error) {
	if err := contextError(ctx); err != nil {
		return nil, err
	}

	s.mu.RLock()
	items := make([]Record, 0, len(s.entries))
	for _, item := range s.entries {
		items = append(items, item.record)
	}
	s.mu.RUnlock()

	if err := contextError(ctx); err != nil {
		return nil, err
	}
	sort.Slice(items, func(i, j int) bool {
		return items[i].CredentialID < items[j].CredentialID
	})
	return items, nil
}

func (s *Service) Delete(ctx context.Context, credentialID string) (DeleteResponse, error) {
	if err := contextError(ctx); err != nil {
		return DeleteResponse{}, err
	}
	if !validCredentialID(credentialID) {
		return DeleteResponse{}, invalid("credential_id must match the required format")
	}

	s.mu.Lock()
	item, exists := s.entries[credentialID]
	if !exists {
		s.mu.Unlock()
		return DeleteResponse{}, apperrors.New("NOT_FOUND", "credential not found", http.StatusNotFound, false)
	}
	credentialRef := item.record.CredentialRef
	zeroEntry(item)
	delete(s.entries, credentialID)
	s.mu.Unlock()

	return DeleteResponse{
		CredentialID:  credentialID,
		CredentialRef: credentialRef,
		Deleted:       true,
		DeletedAt:     time.Now().UTC(),
	}, nil
}

func (s *Service) ResolveSSH(ctx context.Context, credentialRef string) (config.SSHCredential, error) {
	if err := contextError(ctx); err != nil {
		return config.SSHCredential{}, err
	}

	credentialID, runtimeNamespace, valid := parseRuntimeCredentialRef(credentialRef)
	if !runtimeNamespace {
		if s.fallback == nil {
			return config.SSHCredential{}, errors.New("credential resolver is not configured")
		}
		credential, err := s.fallback.ResolveSSH(ctx, credentialRef)
		if err != nil {
			return config.SSHCredential{}, err
		}
		credential.PrivateKey = cloneBytes(credential.PrivateKey)
		credential.PrivateKeyPassphrase = cloneBytes(credential.PrivateKeyPassphrase)
		return credential, nil
	}
	if !valid {
		return config.SSHCredential{}, errors.New("runtime credential_ref is invalid")
	}

	s.mu.RLock()
	item, exists := s.entries[credentialID]
	if !exists {
		s.mu.RUnlock()
		return config.SSHCredential{}, errors.New("runtime credential is not registered")
	}
	credential := config.SSHCredential{
		User:                 item.record.SSHUser,
		PrivateKey:           cloneBytes(item.privateKey),
		PrivateKeyPassphrase: cloneBytes(item.privateKeyPassphrase),
		Password:             string(item.password),
		HostKeyFingerprint:   item.record.HostKeyFingerprint,
		Timeout:              time.Duration(item.record.SSHTimeoutSeconds) * time.Second,
	}
	s.mu.RUnlock()

	if err := contextError(ctx); err != nil {
		zeroBytes(credential.PrivateKey)
		zeroBytes(credential.PrivateKeyPassphrase)
		return config.SSHCredential{}, err
	}
	return credential, nil
}

func newEntry(req CreateRequest, defaultTimeoutSeconds int) (*entry, error) {
	if !validCredentialID(req.CredentialID) {
		return nil, invalid("credential_id must match the required format")
	}
	if req.CredentialType != CredentialTypeSSH {
		return nil, invalid("credential_type must be ssh")
	}

	if strings.ContainsAny(req.SSHUser, "\r\n\t") {
		return nil, invalid("ssh_user contains unsupported control characters")
	}
	if utf8.RuneCountInString(req.SSHUser) > MaxSSHUserLength {
		return nil, invalid("ssh_user exceeds the maximum length")
	}
	sshUser := strings.TrimSpace(req.SSHUser)
	if sshUser == "" {
		return nil, invalid("ssh_user is required")
	}
	if !config.IsValidSSHHostKeyFingerprint(req.HostKeyFingerprint) {
		return nil, invalid("host_key_fingerprint must be a canonical OpenSSH SHA256 fingerprint")
	}

	timeoutSeconds := defaultTimeoutSeconds
	if req.SSHTimeoutSeconds != nil {
		timeoutSeconds = *req.SSHTimeoutSeconds
	}
	if timeoutSeconds < 1 || timeoutSeconds > MaxSSHTimeoutSeconds {
		return nil, invalid("ssh_timeout_seconds must be between 1 and 300")
	}

	item := &entry{
		record: Record{
			CredentialID:       req.CredentialID,
			CredentialRef:      RuntimeCredentialPrefix + req.CredentialID,
			CredentialType:     CredentialTypeSSH,
			AuthType:           req.AuthType,
			SSHUser:            sshUser,
			HostKeyFingerprint: req.HostKeyFingerprint,
			SSHTimeoutSeconds:  timeoutSeconds,
			Persistent:         false,
			CreatedAt:          time.Now().UTC(),
		},
	}

	switch req.AuthType {
	case AuthTypePrivateKey:
		if req.Password != nil {
			return nil, invalid("password is not allowed for private_key authentication")
		}
		if req.PrivateKey == nil || *req.PrivateKey == "" {
			return nil, invalid("private_key is required for private_key authentication")
		}
		if len(*req.PrivateKey) > MaxPrivateKeyBytes {
			return nil, invalid("private_key exceeds the maximum length")
		}
		passphrase := ""
		if req.PrivateKeyPassphrase != nil {
			passphrase = *req.PrivateKeyPassphrase
			if passphrase == "" {
				return nil, invalid("private_key_passphrase must not be empty when provided")
			}
		}
		if utf8.RuneCountInString(passphrase) > MaxPassphraseRunes {
			return nil, invalid("private_key_passphrase exceeds the maximum length")
		}

		item.privateKey = []byte(*req.PrivateKey)
		item.privateKeyPassphrase = []byte(passphrase)
		if err := validatePrivateKey(item.privateKey, item.privateKeyPassphrase); err != nil {
			zeroEntry(item)
			return nil, err
		}
	case AuthTypePassword:
		if req.PrivateKey != nil {
			return nil, invalid("private_key is not allowed for password authentication")
		}
		if req.PrivateKeyPassphrase != nil {
			return nil, invalid("private_key_passphrase is not allowed for password authentication")
		}
		if req.Password == nil || *req.Password == "" {
			return nil, invalid("password is required for password authentication")
		}
		if utf8.RuneCountInString(*req.Password) > MaxPasswordRunes {
			return nil, invalid("password exceeds the maximum length")
		}
		item.password = []byte(*req.Password)
	default:
		return nil, invalid("auth_type must be private_key or password")
	}

	return item, nil
}

func validatePrivateKey(privateKey, passphrase []byte) error {
	var err error
	if len(passphrase) > 0 {
		_, err = ssh.ParsePrivateKeyWithPassphrase(privateKey, passphrase)
	} else {
		_, err = ssh.ParsePrivateKey(privateKey)
	}
	if err != nil {
		return invalid("private_key could not be parsed")
	}
	return nil
}

func parseRuntimeCredentialRef(credentialRef string) (credentialID string, runtimeNamespace, valid bool) {
	parsed, err := url.Parse(credentialRef)
	if err != nil {
		return "", strings.HasPrefix(strings.ToLower(credentialRef), "cred://runtime"), false
	}
	runtimeNamespace = strings.EqualFold(parsed.Scheme, "cred") && strings.EqualFold(parsed.Hostname(), "runtime")
	if !runtimeNamespace {
		return "", false, false
	}
	if parsed.Scheme != "cred" || parsed.Host != "runtime" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.ForceQuery {
		return "", true, false
	}
	credentialID = strings.TrimPrefix(parsed.Path, "/")
	if !validCredentialID(credentialID) || credentialRef != RuntimeCredentialPrefix+credentialID {
		return "", true, false
	}
	return credentialID, true, true
}

func validCredentialID(credentialID string) bool {
	return len(credentialID) >= 2 && len(credentialID) <= MaxCredentialIDLength && credentialIDPattern.MatchString(credentialID)
}

func invalid(message string) error {
	return apperrors.New(model.ErrTargetProfileInvalid, message, http.StatusBadRequest, false)
}

func (s *Service) defaultTimeout() int {
	if s.defaultTimeoutSeconds < 1 || s.defaultTimeoutSeconds > MaxSSHTimeoutSeconds {
		return defaultSSHTimeoutSeconds
	}
	return s.defaultTimeoutSeconds
}

func normalizeDefaultTimeout(timeout time.Duration) int {
	if timeout <= 0 || timeout > MaxSSHTimeoutSeconds*time.Second {
		return defaultSSHTimeoutSeconds
	}
	seconds := int((timeout + time.Second - 1) / time.Second)
	if seconds < 1 || seconds > MaxSSHTimeoutSeconds {
		return defaultSSHTimeoutSeconds
	}
	return seconds
}

func contextError(ctx context.Context) error {
	if ctx == nil {
		return nil
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
		return nil
	}
}

func cloneBytes(value []byte) []byte {
	if len(value) == 0 {
		return nil
	}
	return append([]byte(nil), value...)
}

func zeroEntry(item *entry) {
	if item == nil {
		return
	}
	zeroBytes(item.privateKey)
	zeroBytes(item.privateKeyPassphrase)
	zeroBytes(item.password)
	item.privateKey = nil
	item.privateKeyPassphrase = nil
	item.password = nil
}

func zeroBytes(value []byte) {
	for i := range value {
		value[i] = 0
	}
}
