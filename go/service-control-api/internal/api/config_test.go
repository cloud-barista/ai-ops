package api

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/viper"
)

func TestNewServerConfigDefaultsDeploymentAdapterToMock(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_DEPLOYMENT_ADAPTER", "")

	config := NewServerConfig()

	if config.DeploymentAdapterMode != "mock" {
		t.Fatalf(
			"deployment adapter mode = %q, want mock",
			config.DeploymentAdapterMode,
		)
	}
}

func TestNewServerConfigDefaultsExecutionAuditToRequiredLocalEvidence(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_EXECUTION_AUDIT_DIR", "")

	config := NewServerConfig()

	if !config.ExecutionAuditRequired {
		t.Fatal("execution audit must be required by default")
	}
	expected := filepath.Join(config.RepoRoot, "runs", "trusted-automation")
	if config.ExecutionAuditDir != expected {
		t.Fatalf("execution audit dir = %q, want %q", config.ExecutionAuditDir, expected)
	}
}

func TestNewServerConfigResolvesRelativeExecutionAuditDirectory(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_EXECUTION_AUDIT_DIR", filepath.Join("local-evidence", "trusted"))
	t.Setenv("AIOPS_EXECUTION_AUDIT_REQUIRED", "false")

	config := NewServerConfig()

	if config.ExecutionAuditRequired {
		t.Fatal("best-effort execution audit setting was not read")
	}
	expected := filepath.Join(config.RepoRoot, "local-evidence", "trusted")
	if config.ExecutionAuditDir != expected {
		t.Fatalf("execution audit dir = %q, want %q", config.ExecutionAuditDir, expected)
	}
}

func TestNewServerConfigReadsHandoffDeploymentAdapter(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_DEPLOYMENT_ADAPTER", " HANDOFF ")

	config := NewServerConfig()

	if config.DeploymentAdapterMode != "handoff" {
		t.Fatalf(
			"deployment adapter mode = %q, want handoff",
			config.DeploymentAdapterMode,
		)
	}
}

func TestValidateServerConfigRejectsUnknownDeploymentAdapter(t *testing.T) {
	config := NewServerConfig()
	config.DeploymentAdapterMode = "external-http"

	err := ValidateServerConfig(config)
	if err == nil {
		t.Fatal("unknown deployment adapter mode was accepted")
	}
	if strings.Contains(err.Error(), "http") {
		t.Fatalf("validation error exposes adapter input: %v", err)
	}
}

func TestValidateServerConfigRequiresAuditDirectoryInStrictMode(t *testing.T) {
	config := NewServerConfig()
	config.ExecutionAuditRequired = true
	config.ExecutionAuditDir = ""

	if err := ValidateServerConfig(config); err == nil {
		t.Fatal("strict audit mode accepted an empty directory")
	}
}

func resetConfigTestEnvironment(t *testing.T) {
	t.Helper()
	viper.Reset()
	t.Cleanup(viper.Reset)
}
