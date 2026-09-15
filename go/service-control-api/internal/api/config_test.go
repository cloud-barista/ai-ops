package api

import (
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

func TestNewServerConfigDefaultsRequirementAnalysisToLocalRule(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_REQUIREMENT_ANALYSIS_MODE", "")

	config := NewServerConfig()

	if config.RequirementAnalysisMode != requirementAnalysisModeLocalRule {
		t.Fatalf(
			"requirement analysis mode = %q, want local_rule",
			config.RequirementAnalysisMode,
		)
	}
}

func TestValidateServerConfigRejectsUnknownRequirementAnalysisMode(t *testing.T) {
	config := NewServerConfig()
	config.RequirementAnalysisMode = "automatic_fallback"

	err := ValidateServerConfig(config)
	if err == nil {
		t.Fatal("unknown requirement analysis mode was accepted")
	}
}

func resetConfigTestEnvironment(t *testing.T) {
	t.Helper()
	viper.Reset()
	t.Cleanup(viper.Reset)
}
