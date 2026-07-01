package cpuvm

import (
	"context"
	"net/url"
	"path/filepath"
	"strings"
	"testing"

	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/model"
)

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

func localFileURI(path string) string {
	normalized := filepath.ToSlash(path)
	if strings.HasPrefix(normalized, "/") {
		return (&url.URL{Scheme: "file", Path: normalized}).String()
	}
	return (&url.URL{Scheme: "file", Path: "/" + normalized}).String()
}
