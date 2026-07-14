package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/khu/ai-app-deployer/internal/config"
	"golang.org/x/crypto/ssh"
	"golang.org/x/term"
)

const (
	credentialSecretMaxBytes = 4096
	credentialKeyMaxBytes    = 65536
)

type credentialCreateRequest struct {
	CredentialID         string `json:"credential_id"`
	CredentialType       string `json:"credential_type"`
	AuthType             string `json:"auth_type"`
	SSHUser              string `json:"ssh_user"`
	HostKeyFingerprint   string `json:"host_key_fingerprint"`
	PrivateKey           string `json:"private_key,omitempty"`
	PrivateKeyPassphrase string `json:"private_key_passphrase,omitempty"`
	Password             string `json:"password,omitempty"`
	SSHTimeoutSeconds    int    `json:"ssh_timeout_seconds"`
}

// credentialRecord is deliberately an allowlist. Never replace it with a
// map[string]any or print the raw Credential API response: a faulty server
// response must not make a write-only Secret visible in the CLI.
type credentialRecord struct {
	CredentialID       string `json:"credential_id"`
	CredentialRef      string `json:"credential_ref"`
	CredentialType     string `json:"credential_type"`
	AuthType           string `json:"auth_type"`
	SSHUser            string `json:"ssh_user"`
	HostKeyFingerprint string `json:"host_key_fingerprint"`
	SSHTimeoutSeconds  int    `json:"ssh_timeout_seconds"`
	Persistent         bool   `json:"persistent"`
	CreatedAt          string `json:"created_at"`
}

type credentialDeleteRecord struct {
	CredentialID  string `json:"credential_id"`
	CredentialRef string `json:"credential_ref"`
	Deleted       bool   `json:"deleted"`
	DeletedAt     string `json:"deleted_at"`
}

func (s *Shell) credentials(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("사용법: credentials list | add [options] | delete <credential-id> --yes")
	}
	switch strings.ToLower(args[0]) {
	case "list", "ls":
		if len(args) != 1 {
			return errors.New("사용법: credentials list")
		}
		return s.showCredentialList(ctx)
	case "add", "create":
		return s.addCredential(ctx, args[1:])
	case "delete", "remove", "rm":
		if len(args) != 3 || (args[2] != "--yes" && args[2] != "-y") {
			return errors.New("사용법: credentials delete <credential-id> --yes")
		}
		return s.deleteCredential(ctx, args[1])
	default:
		return fmt.Errorf("알 수 없는 credentials 작업 %q입니다", args[0])
	}
}

func (s *Shell) showCredentialList(ctx context.Context) error {
	return s.showList(ctx, "/api/v1/credentials", []column{
		{title: "Credential ID", path: "credential_id"},
		{title: "Credential ref", path: "credential_ref"},
		{title: "종류", path: "credential_type"},
		{title: "인증", path: "auth_type"},
		{title: "SSH 사용자", path: "ssh_user"},
		{title: "Host key fingerprint", path: "host_key_fingerprint"},
		{title: "Timeout(s)", path: "ssh_timeout_seconds"},
		{title: "영속", path: "persistent"},
		{title: "등록 시각", path: "created_at"},
	})
}

func (s *Shell) addCredential(ctx context.Context, args []string) error {
	if hasCredentialSecretArgument(args) {
		return errors.New("비밀값은 명령행 인자로 전달할 수 없습니다. --private-key-file, --password-stdin 또는 --passphrase-stdin을 사용하세요")
	}

	flags := flag.NewFlagSet("credentials add", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	var credentialID, sshUser, authType, privateKeyFile, hostKeyFingerprint string
	var passwordStdin, passphraseStdin bool
	var timeout int
	flags.StringVar(&credentialID, "id", "", "Credential ID")
	flags.StringVar(&credentialID, "credential-id", "", "Credential ID")
	flags.StringVar(&sshUser, "user", "", "SSH user")
	flags.StringVar(&sshUser, "ssh-user", "", "SSH user")
	flags.StringVar(&authType, "auth", "", "private_key or password")
	flags.StringVar(&authType, "auth-type", "", "private_key or password")
	flags.StringVar(&hostKeyFingerprint, "host-key-fingerprint", "", "expected SSH host key fingerprint (SHA256:base64)")
	flags.StringVar(&privateKeyFile, "private-key-file", "", "PEM private key file")
	flags.BoolVar(&passwordStdin, "password-stdin", false, "read password from stdin when stdin is not a TTY")
	flags.BoolVar(&passphraseStdin, "passphrase-stdin", false, "read private key passphrase from stdin when stdin is not a TTY")
	flags.BoolVar(&passphraseStdin, "private-key-passphrase-stdin", false, "read private key passphrase from stdin when stdin is not a TTY")
	flags.IntVar(&timeout, "timeout", 30, "SSH timeout seconds")
	flags.IntVar(&timeout, "ssh-timeout-seconds", 30, "SSH timeout seconds")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 {
		return errors.New("사용법: credentials add --id <id> --user <user> --host-key-fingerprint <SHA256:base64> --auth <private_key|password> [--private-key-file <path>|--password-stdin] [--timeout 30]")
	}
	if timeout < 1 || timeout > 300 {
		return errors.New("SSH timeout은 1~300초여야 합니다")
	}

	var err error
	if credentialID == "" {
		if !s.canPromptCredentialMetadata() {
			return errors.New("비대화형 등록에는 --id, --user, --host-key-fingerprint, --auth를 모두 지정하세요")
		}
		credentialID, err = s.prompt("Credential ID", "", true)
		if err != nil {
			return err
		}
	}
	if sshUser == "" {
		if !s.canPromptCredentialMetadata() {
			return errors.New("비대화형 등록에는 --id, --user, --host-key-fingerprint, --auth를 모두 지정하세요")
		}
		sshUser, err = s.prompt("SSH 사용자", "", true)
		if err != nil {
			return err
		}
	}
	if authType == "" {
		if !s.canPromptCredentialMetadata() {
			return errors.New("비대화형 등록에는 --id, --user, --host-key-fingerprint, --auth를 모두 지정하세요")
		}
		authType, err = s.promptChoice("인증 방식", "private_key", "private_key", "password")
		if err != nil {
			return err
		}
	}
	if hostKeyFingerprint == "" {
		if !s.canPromptCredentialMetadata() {
			return errors.New("비대화형 등록에는 --id, --user, --host-key-fingerprint, --auth를 모두 지정하세요")
		}
		hostKeyFingerprint, err = s.prompt("Host key fingerprint (SHA256:base64)", "", true)
		if err != nil {
			return err
		}
	}
	hostKeyFingerprint = strings.TrimSpace(hostKeyFingerprint)
	if !config.IsValidSSHHostKeyFingerprint(hostKeyFingerprint) {
		return errors.New("--host-key-fingerprint는 SHA256:base64 형식의 SSH host key fingerprint여야 합니다")
	}
	authType = strings.ToLower(strings.TrimSpace(authType))
	if authType != "private_key" && authType != "password" {
		return errors.New("--auth는 private_key 또는 password여야 합니다")
	}

	request := credentialCreateRequest{
		CredentialID:       strings.TrimSpace(credentialID),
		CredentialType:     "ssh",
		AuthType:           authType,
		SSHUser:            strings.TrimSpace(sshUser),
		HostKeyFingerprint: hostKeyFingerprint,
		SSHTimeoutSeconds:  timeout,
	}
	if err := s.populateCredentialSecrets(&request, privateKeyFile, passwordStdin, passphraseStdin); err != nil {
		return err
	}
	requestBody, err := json.Marshal(request)
	request.PrivateKey = ""
	request.PrivateKeyPassphrase = ""
	request.Password = ""
	if err != nil {
		return fmt.Errorf("Credential 요청 인코딩: %w", err)
	}
	defer clearBytes(requestBody)

	resp, err := s.call(ctx, http.MethodPost, "/api/v1/credentials", requestBody)
	if err != nil {
		return err
	}
	if err := s.printCredentialRecord(resp.body); err != nil {
		return err
	}
	if s.interactive {
		s.warning("Credential은 현재 대화형 CLI 프로세스에서만 유지됩니다. 이 세션에서 이어서 배포하세요.")
	} else {
		s.warning("이 단발 명령이 끝나면 Credential이 소멸합니다. 후속 배포에는 대화형 CLI에서 등록하세요.")
	}
	return nil
}

func (s *Shell) populateCredentialSecrets(request *credentialCreateRequest, privateKeyFile string, passwordStdin, passphraseStdin bool) error {
	if request.AuthType == "password" {
		if privateKeyFile != "" || passphraseStdin {
			return errors.New("password 인증에는 --private-key-file 또는 --passphrase-stdin을 함께 사용할 수 없습니다")
		}
		secret, err := s.readCredentialSecret("SSH password", passwordStdin)
		if err != nil {
			return err
		}
		defer clearBytes(secret)
		if err := validateCredentialSecret(secret, "SSH password"); err != nil {
			return err
		}
		request.Password = string(secret)
		return nil
	}

	if passwordStdin {
		return errors.New("private_key 인증에는 --password-stdin을 사용할 수 없습니다")
	}
	if strings.TrimSpace(privateKeyFile) == "" {
		return errors.New("private_key 인증에는 --private-key-file이 필요합니다")
	}
	privateKey, err := os.ReadFile(privateKeyFile)
	if err != nil {
		return errors.New("private key 파일을 읽을 수 없습니다")
	}
	defer clearBytes(privateKey)
	if len(privateKey) == 0 || len(bytes.TrimSpace(privateKey)) == 0 {
		return errors.New("private key 파일이 비어 있습니다")
	}
	if len(privateKey) > credentialKeyMaxBytes {
		return fmt.Errorf("private key는 %d bytes 이하여야 합니다", credentialKeyMaxBytes)
	}
	request.PrivateKey = string(privateKey)

	if passphraseStdin || privateKeyNeedsPassphrase(privateKey) {
		passphrase, err := s.readCredentialSecret("Private key passphrase", passphraseStdin)
		if err != nil {
			return err
		}
		defer clearBytes(passphrase)
		if err := validateCredentialSecret(passphrase, "Private key passphrase"); err != nil {
			return err
		}
		request.PrivateKeyPassphrase = string(passphrase)
	}
	return nil
}

func (s *Shell) readCredentialSecret(label string, allowStdin bool) ([]byte, error) {
	if allowStdin && !s.inputIsTerminal() {
		line, err := s.reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return nil, fmt.Errorf("%s 표준입력 읽기: %w", label, err)
		}
		return []byte(strings.TrimRight(line, "\r\n")), nil
	}
	if s.readSecret == nil {
		return nil, errors.New("TTY 비밀값 입력기를 사용할 수 없습니다")
	}
	return s.readSecret(label)
}

func (s *Shell) readTerminalSecret(label string) ([]byte, error) {
	file, ok := s.input.(*os.File)
	if !ok || !term.IsTerminal(int(file.Fd())) {
		return nil, fmt.Errorf("%s 입력에는 TTY가 필요합니다. 비대화형 실행에서는 명시적인 --password-stdin 또는 --passphrase-stdin을 사용하세요", label)
	}
	fmt.Fprintf(s.out, "%s: ", s.paint(ansiCyan, label))
	secret, err := term.ReadPassword(int(file.Fd()))
	fmt.Fprintln(s.out)
	if err != nil {
		return nil, fmt.Errorf("%s 입력: %w", label, err)
	}
	return secret, nil
}

func (s *Shell) inputIsTerminal() bool {
	file, ok := s.input.(*os.File)
	return ok && term.IsTerminal(int(file.Fd()))
}

func (s *Shell) canPromptCredentialMetadata() bool {
	return s.interactive || s.inputIsTerminal()
}

func (s *Shell) deleteCredential(ctx context.Context, credentialID string) error {
	resp, err := s.call(ctx, http.MethodDelete, "/api/v1/credentials/"+url.PathEscape(credentialID), nil)
	if err != nil {
		return err
	}
	var result credentialDeleteRecord
	if err := json.Unmarshal(resp.body, &result); err != nil {
		return fmt.Errorf("Credential 삭제 응답 디코딩: %w", err)
	}
	return s.printValue(result)
}

func (s *Shell) printCredentialRecord(raw []byte) error {
	var record credentialRecord
	if err := json.Unmarshal(raw, &record); err != nil {
		return fmt.Errorf("Credential 응답 디코딩: %w", err)
	}
	return s.printValue(record)
}

func hasCredentialSecretArgument(args []string) bool {
	for _, arg := range args {
		if !strings.HasPrefix(arg, "-") {
			continue
		}
		name := strings.SplitN(strings.TrimLeft(arg, "-"), "=", 2)[0]
		switch name {
		case "password", "private-key", "private-key-passphrase", "passphrase":
			return true
		}
		// A value attached to a bool stdin flag is both unnecessary and easy to
		// misuse as an inline Secret. Reject it without echoing that value.
		if strings.Contains(arg, "=") && (name == "password-stdin" || name == "passphrase-stdin" || name == "private-key-passphrase-stdin") {
			return true
		}
	}
	return false
}

func validateCredentialSecret(secret []byte, label string) error {
	if len(secret) == 0 {
		return fmt.Errorf("%s이(가) 비어 있습니다", label)
	}
	if len(secret) > credentialSecretMaxBytes {
		return fmt.Errorf("%s은(는) %d bytes 이하여야 합니다", label, credentialSecretMaxBytes)
	}
	return nil
}

func privateKeyNeedsPassphrase(privateKey []byte) bool {
	if bytes.Contains(bytes.ToUpper(privateKey), []byte("ENCRYPTED")) {
		return true
	}
	_, err := ssh.ParseRawPrivateKey(privateKey)
	var missing *ssh.PassphraseMissingError
	return errors.As(err, &missing)
}

func clearBytes(value []byte) {
	for index := range value {
		value[index] = 0
	}
}
