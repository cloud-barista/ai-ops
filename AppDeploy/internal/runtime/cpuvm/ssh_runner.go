package cpuvm

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
	"golang.org/x/crypto/ssh"
)

type SSHRunner struct {
	resolver       config.SSHCredentialResolver
	defaultTimeout time.Duration
}

func NewSSHRunner(resolver config.SSHCredentialResolver, defaultTimeout time.Duration) *SSHRunner {
	if defaultTimeout == 0 {
		defaultTimeout = 30 * time.Second
	}
	return &SSHRunner{resolver: resolver, defaultTimeout: defaultTimeout}
}

func (r *SSHRunner) Run(ctx context.Context, target model.TargetProfile, command Command) (Result, error) {
	credential, err := r.resolver.ResolveSSH(ctx, target.VM.CredentialRef)
	if err != nil {
		return Result{}, err
	}
	timeout := credential.Timeout
	if timeout == 0 {
		timeout = r.defaultTimeout
	}

	auth, err := authMethods(credential)
	if err != nil {
		return Result{}, err
	}

	port := target.VM.SSHPort
	if port == 0 {
		port = 22
	}
	clientConfig := &ssh.ClientConfig{
		User:            credential.User,
		Auth:            auth,
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         timeout,
	}
	address := net.JoinHostPort(target.VM.Host, strconv.Itoa(port))
	client, err := dialSSH(ctx, "tcp", address, clientConfig)
	if err != nil {
		return Result{}, fmt.Errorf("ssh dial failed for target %s: %w", target.TargetProfileID, err)
	}
	defer client.Close()

	if command.Name == "prepare-artifact" {
		return r.prepareArtifact(ctx, client, target, command)
	}
	return runSSHCommand(ctx, client, target, buildShellCommand(command))
}

func (r *SSHRunner) prepareArtifact(ctx context.Context, client *ssh.Client, target model.TargetProfile, command Command) (Result, error) {
	if len(command.Args) < 2 {
		return Result{}, fmt.Errorf("prepare-artifact requires artifact uri and target path")
	}
	artifactURI := command.Args[0]
	targetPath := command.Args[1]

	localPath, isLocalFile, err := localPathFromFileURI(artifactURI)
	if err != nil {
		return Result{}, err
	}
	if isLocalFile {
		remotePath, err := uploadLocalFile(ctx, client, target, localPath, targetPath)
		if err != nil {
			return Result{}, err
		}
		return Result{Output: "uploaded local artifact to " + remotePath}, nil
	}
	return runSSHCommand(ctx, client, target, buildShellCommand(command))
}

func runSSHCommand(ctx context.Context, client *ssh.Client, target model.TargetProfile, shellCommand string) (Result, error) {
	session, err := client.NewSession()
	if err != nil {
		return Result{}, fmt.Errorf("ssh session failed for target %s: %w", target.TargetProfileID, err)
	}
	defer session.Close()

	type result struct {
		output []byte
		err    error
	}
	ch := make(chan result, 1)
	go func() {
		output, err := session.CombinedOutput(shellCommand)
		ch <- result{output: output, err: err}
	}()
	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	case result := <-ch:
		if result.err != nil {
			return Result{}, fmt.Errorf("ssh command failed for target %s: %w", target.TargetProfileID, result.err)
		}
		return Result{Output: strings.TrimSpace(string(result.output))}, nil
	}
}

func uploadLocalFile(ctx context.Context, client *ssh.Client, target model.TargetProfile, localPath, remoteDir string) (string, error) {
	file, err := os.Open(localPath)
	if err != nil {
		return "", fmt.Errorf("local artifact could not be opened")
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		return "", fmt.Errorf("local artifact could not be inspected")
	}
	if info.IsDir() {
		return "", fmt.Errorf("local artifact is a directory")
	}

	remoteFile := path.Join(remoteDir, filepath.Base(localPath))
	command := "mkdir -p " + shellQuote(remoteDir) +
		" && cat > " + shellQuote(remoteFile) +
		" && chmod +x " + shellQuote(remoteFile)

	session, err := client.NewSession()
	if err != nil {
		return "", fmt.Errorf("ssh session failed for target %s: %w", target.TargetProfileID, err)
	}
	defer session.Close()

	stdin, err := session.StdinPipe()
	if err != nil {
		return "", fmt.Errorf("ssh stdin failed for target %s: %w", target.TargetProfileID, err)
	}
	var stderr bytes.Buffer
	session.Stderr = &stderr

	if err := session.Start(command); err != nil {
		return "", fmt.Errorf("ssh upload command failed for target %s: %w", target.TargetProfileID, err)
	}
	copyCh := make(chan error, 1)
	go func() {
		_, copyErr := io.Copy(stdin, file)
		closeErr := stdin.Close()
		if copyErr != nil {
			copyCh <- copyErr
			return
		}
		copyCh <- closeErr
	}()

	select {
	case <-ctx.Done():
		_ = stdin.Close()
		return "", ctx.Err()
	case err := <-copyCh:
		if err != nil {
			return "", fmt.Errorf("local artifact upload failed")
		}
	}
	if err := session.Wait(); err != nil {
		if stderr.Len() > 0 {
			return "", fmt.Errorf("ssh upload failed for target %s: %s", target.TargetProfileID, strings.TrimSpace(stderr.String()))
		}
		return "", fmt.Errorf("ssh upload failed for target %s: %w", target.TargetProfileID, err)
	}
	return remoteFile, nil
}

func authMethods(credential config.SSHCredential) ([]ssh.AuthMethod, error) {
	var methods []ssh.AuthMethod
	if credential.PrivateKeyPath != "" {
		key, err := os.ReadFile(credential.PrivateKeyPath)
		if err != nil {
			return nil, fmt.Errorf("ssh private key could not be read")
		}
		signer, err := ssh.ParsePrivateKey(key)
		if err != nil {
			return nil, fmt.Errorf("ssh private key could not be parsed")
		}
		methods = append(methods, ssh.PublicKeys(signer))
	}
	if credential.Password != "" {
		methods = append(methods, ssh.Password(credential.Password))
	}
	if len(methods) == 0 {
		return nil, fmt.Errorf("ssh auth method is not configured")
	}
	return methods, nil
}

func dialSSH(ctx context.Context, network, address string, config *ssh.ClientConfig) (*ssh.Client, error) {
	type result struct {
		client *ssh.Client
		err    error
	}
	ch := make(chan result, 1)
	go func() {
		client, err := ssh.Dial(network, address, config)
		ch <- result{client: client, err: err}
	}()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case result := <-ch:
		return result.client, result.err
	}
}

func buildShellCommand(command Command) string {
	if command.Name == "prepare-artifact" && len(command.Args) >= 2 {
		targetPath := command.Args[1]
		artifactURI := command.Args[0]
		return "mkdir -p " + shellQuote(targetPath) + " && printf '%s' " + shellQuote(artifactURI) + " > " + shellQuote(targetPath+"/.artifact_uri")
	}
	if command.Name == "stop-process" && len(command.Args) >= 1 {
		return buildStopCommand(command.Args[0])
	}
	parts := append([]string{command.Name}, command.Args...)
	quoted := make([]string, 0, len(parts))
	for _, part := range parts {
		quoted = append(quoted, shellQuote(part))
	}
	joined := strings.Join(quoted, " ")
	if command.WorkingDir != "" {
		launcher := "echo $$ > .aiapp.pid; exec " + joined
		quotedLauncher := shellQuote(launcher)
		return "cd " + shellQuote(command.WorkingDir) + " && if command -v setsid >/dev/null 2>&1; then setsid -f sh -c " + quotedLauncher + " >/dev/null 2>&1 </dev/null; else (nohup sh -c " + quotedLauncher + " >/dev/null 2>&1 </dev/null &); fi; echo accepted"
	}
	return joined
}

func buildStopCommand(workingDir string) string {
	script := strings.Join([]string{
		"dir=$(pwd)",
		"pids=''",
		"if [ -f .aiapp.pid ]; then pids=$(cat .aiapp.pid 2>/dev/null | tr -cs '0-9\n' '\n' || true); fi",
		"fallback=$(pgrep -f \"$dir/\" 2>/dev/null | grep -v \"^$$$\" || true)",
		"pids=$(printf '%s\n%s\n' \"$pids\" \"$fallback\" | awk 'NF && !seen[$0]++')",
		"safe_pids=''",
		"for pid in $pids; do case \"$pid\" in ''|*[!0-9]*) continue;; esac; args=$(ps -p \"$pid\" -o args= 2>/dev/null || true); cwd=$(readlink \"/proc/$pid/cwd\" 2>/dev/null || true); if [ \"$cwd\" = \"$dir\" ]; then safe_pids=\"$safe_pids $pid\"; continue; fi; case \"$args\" in *\"$dir/\"*) safe_pids=\"$safe_pids $pid\";; esac; done",
		"if [ -z \"$safe_pids\" ]; then rm -f .aiapp.pid; echo no-process; exit 0; fi",
		"for pid in $safe_pids; do kill -TERM \"$pid\" 2>/dev/null || true; done",
		"sleep 1",
		"for pid in $safe_pids; do if kill -0 \"$pid\" 2>/dev/null; then kill -KILL \"$pid\" 2>/dev/null || true; fi; done",
		"rm -f .aiapp.pid",
		"echo stopped",
	}, "; ")
	return "cd " + shellQuote(workingDir) + " && sh -c " + shellQuote(script)
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func localPathFromFileURI(rawURI string) (string, bool, error) {
	parsed, err := url.Parse(rawURI)
	if err != nil {
		return "", false, fmt.Errorf("artifact uri is invalid")
	}
	if parsed.Scheme != "file" {
		return "", false, nil
	}
	if parsed.Host != "" && parsed.Host != "localhost" {
		return "", false, fmt.Errorf("file artifact uri host is not supported")
	}
	localPath, err := url.PathUnescape(parsed.Path)
	if err != nil {
		return "", false, fmt.Errorf("file artifact uri path is invalid")
	}
	if len(localPath) >= 3 && localPath[0] == '/' && localPath[2] == ':' {
		localPath = localPath[1:]
	}
	localPath = filepath.FromSlash(localPath)
	if strings.TrimSpace(localPath) == "" {
		return "", false, fmt.Errorf("file artifact uri path is empty")
	}
	return localPath, true, nil
}
