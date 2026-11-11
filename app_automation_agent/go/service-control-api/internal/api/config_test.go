package api

import (
	"os"
	"strings"
	"testing"

	"github.com/spf13/viper"
)

func TestNewServerConfigDefaultsToFocusedAPI(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_LEGACY_API_ENABLED", "")
	t.Setenv("AIOPS_APPDEPLOY_POC_ENABLED", "")

	config := NewServerConfig()

	if config.LegacyAPIEnabled || config.AppDeployPoCEnabled {
		t.Fatal("legacy and AppDeploy PoC APIs must be opt-in")
	}
	if _, err := os.Stat(config.FocusedOpenAPIPath); err != nil {
		t.Fatalf("focused OpenAPI path is unavailable: %v", err)
	}
}

func TestAppDeployPoCConfigurationIsExclusive(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_APPDEPLOY_POC_ENABLED", "true")
	t.Setenv("AIOPS_APPDEPLOY_BASE_URL", "http://127.0.0.1:18150/api/v1")
	t.Setenv("AIOPS_AIAPP_BASE_URL", "")
	t.Setenv("AIOPS_LEGACY_API_ENABLED", "false")
	t.Setenv("AIOPS_DEPLOYMENT_ADAPTER", "mock")
	t.Setenv("AIOPS_REQUIREMENT_ANALYSIS_MODE", "local_rule")
	config := NewServerConfig()
	if !config.AppDeployPoCEnabled {
		t.Fatal("AppDeploy PoC mode was not read from the environment")
	}
	if err := ValidateServerConfig(config); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name, wantError string
		change          func(*ServerConfig)
	}{
		{"missing AppDeploy URL", "AIOPS_APPDEPLOY_BASE_URL", func(c *ServerConfig) { c.AppDeployBaseURL = "" }},
		{"AI-App URL", "AIOPS_AIAPP_BASE_URL", func(c *ServerConfig) { c.AIAppBaseURL = "http://127.0.0.1:8090" }},
		{"all-in-one API", "AIOPS_LEGACY_API_ENABLED", func(c *ServerConfig) { c.LegacyAPIEnabled = true }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			conflict := config
			tc.change(&conflict)
			if err := ValidateServerConfig(conflict); err == nil || !strings.Contains(err.Error(), tc.wantError) {
				t.Fatalf("invalid AppDeploy PoC configuration: %v", err)
			}
		})
	}
}

func TestNewServerConfigReadsOptInScalingDecisionJournal(t *testing.T) {
	resetConfigTestEnvironment(t)
	t.Setenv("AIOPS_SCALING_DECISION_JOURNAL_DIR", "runs/research-decisions")
	config := NewServerConfig()
	if config.ScalingDecisionJournalDir != "runs/research-decisions" {
		t.Fatalf("scaling decision journal directory = %q", config.ScalingDecisionJournalDir)
	}
}

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
