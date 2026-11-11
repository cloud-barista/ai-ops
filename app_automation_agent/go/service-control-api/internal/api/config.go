package api

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/viper"
)

const (
	requirementAnalysisModeLocalRule = "local_rule"
	requirementAnalysisModeQwen      = "qwen"
)

type ServerConfig struct {
	RepoRoot                  string
	OpenAPIPath               string
	FocusedOpenAPIPath        string
	LLMCandidatesPath         string
	ResourceCatalogPath       string
	PlannerGuardPolicyPath    string
	AppDeployBaseURL          string
	AppDeployPoCEnabled       bool
	AppDeploySubmitEnabled    bool
	AppDeployDeliveryDir      string
	AIAppBaseURL              string
	AIAppNamespace            string
	AIAppToken                string
	AIAppEngineRegistryToken  string
	AIAppEngineApprovalToken  string
	FrameworkAccessPolicyFile string
	LLMOpBaseURL              string
	OpsLLMToken               string
	ScalingDecisionJournalDir string
	DeploymentAdapterMode     string
	AppUploadMaxBytes         int64
	BindAddress               string
	AutonomyAdminToken        string
	AgentExecutionTimeout     time.Duration
	LLMOpAllowLiveCompletion  bool
	RequirementAnalysisMode   string
	LegacyAPIEnabled          bool
}

func NewServerConfig() ServerConfig {
	viper.SetEnvPrefix("AIOPS")
	viper.AutomaticEnv()

	repoRoot := viper.GetString("REPO_ROOT")
	if repoRoot == "" {
		repoRoot = findRepoRoot()
	}
	openAPIPath := viper.GetString("OPENAPI_PATH")
	if openAPIPath == "" {
		openAPIPath = filepath.Join(repoRoot, "docs", "submission", "openapi_service_control.yaml")
	}
	focusedOpenAPIPath := viper.GetString("FOCUSED_OPENAPI_PATH")
	if focusedOpenAPIPath == "" {
		focusedOpenAPIPath = filepath.Join(repoRoot, "docs", "openapi_deployment_agent.yaml")
	} else if !filepath.IsAbs(focusedOpenAPIPath) {
		focusedOpenAPIPath = filepath.Join(repoRoot, focusedOpenAPIPath)
	}
	llmCandidatesPath := viper.GetString("LLM_CANDIDATES_PATH")
	if llmCandidatesPath == "" {
		llmCandidatesPath = filepath.Join(repoRoot, "config", "ops_llm_eval_candidates.json")
	} else if !filepath.IsAbs(llmCandidatesPath) {
		llmCandidatesPath = filepath.Join(repoRoot, llmCandidatesPath)
	}
	resourceCatalogPath := viper.GetString("RESOURCE_CATALOG_PATH")
	if resourceCatalogPath == "" {
		resourceCatalogPath = filepath.Join(repoRoot, "config", "mock_resource_catalog.json")
	} else if !filepath.IsAbs(resourceCatalogPath) {
		resourceCatalogPath = filepath.Join(repoRoot, resourceCatalogPath)
	}
	plannerGuardPolicyPath := viper.GetString("PLANNER_GUARD_POLICY_PATH")
	if plannerGuardPolicyPath == "" {
		plannerGuardPolicyPath = filepath.Join(repoRoot, "config", "planner_guard_policy.json")
	} else if !filepath.IsAbs(plannerGuardPolicyPath) {
		plannerGuardPolicyPath = filepath.Join(repoRoot, plannerGuardPolicyPath)
	}
	bindAddress := strings.TrimSpace(viper.GetString("BIND_ADDRESS"))
	if bindAddress == "" {
		bindAddress = "127.0.0.1"
	}
	agentExecutionTimeoutSeconds := viper.GetInt("AGENT_EXECUTION_TIMEOUT_SECONDS")
	if agentExecutionTimeoutSeconds <= 0 {
		agentExecutionTimeoutSeconds = 30
	}
	if agentExecutionTimeoutSeconds > 120 {
		agentExecutionTimeoutSeconds = 120
	}
	appUploadMaxBytes := viper.GetInt64("APP_UPLOAD_MAX_BYTES")
	if appUploadMaxBytes <= 0 {
		appUploadMaxBytes = 50 << 20
	}
	if appUploadMaxBytes > 1<<30 {
		appUploadMaxBytes = 1 << 30
	}
	deploymentAdapterMode := strings.ToLower(
		strings.TrimSpace(viper.GetString("DEPLOYMENT_ADAPTER")),
	)
	if deploymentAdapterMode == "" {
		deploymentAdapterMode = "mock"
	}
	requirementAnalysisMode := strings.ToLower(
		strings.TrimSpace(viper.GetString("REQUIREMENT_ANALYSIS_MODE")),
	)
	if requirementAnalysisMode == "" {
		requirementAnalysisMode = requirementAnalysisModeLocalRule
	}
	return ServerConfig{
		RepoRoot:                  repoRoot,
		OpenAPIPath:               openAPIPath,
		FocusedOpenAPIPath:        focusedOpenAPIPath,
		LLMCandidatesPath:         llmCandidatesPath,
		ResourceCatalogPath:       resourceCatalogPath,
		PlannerGuardPolicyPath:    plannerGuardPolicyPath,
		AppDeployBaseURL:          viper.GetString("APPDEPLOY_BASE_URL"),
		AppDeployPoCEnabled:       viper.GetBool("APPDEPLOY_POC_ENABLED"),
		AppDeploySubmitEnabled:    viper.GetBool("APPDEPLOY_SUBMIT_ENABLED"),
		AppDeployDeliveryDir:      viper.GetString("APPDEPLOY_DELIVERY_DIR"),
		AIAppBaseURL:              strings.TrimSpace(viper.GetString("AIAPP_BASE_URL")),
		AIAppNamespace:            strings.TrimSpace(viper.GetString("AIAPP_NAMESPACE")),
		AIAppToken:                strings.TrimSpace(viper.GetString("AIAPP_TOKEN")),
		AIAppEngineRegistryToken:  strings.TrimSpace(viper.GetString("AIAPP_ENGINE_REGISTRY_TOKEN")),
		AIAppEngineApprovalToken:  strings.TrimSpace(viper.GetString("AIAPP_ENGINE_APPROVAL_TOKEN")),
		FrameworkAccessPolicyFile: strings.TrimSpace(viper.GetString("FRAMEWORK_ACCESS_POLICY_FILE")),
		LLMOpBaseURL:              strings.TrimSpace(viper.GetString("LLMOP_BASE_URL")),
		OpsLLMToken:               strings.TrimSpace(viper.GetString("OPS_LLM_TOKEN")),
		ScalingDecisionJournalDir: viper.GetString("SCALING_DECISION_JOURNAL_DIR"),
		DeploymentAdapterMode:     deploymentAdapterMode,
		AppUploadMaxBytes:         appUploadMaxBytes,
		BindAddress:               bindAddress,
		AutonomyAdminToken:        viper.GetString("AUTONOMY_ADMIN_TOKEN"),
		AgentExecutionTimeout:     time.Duration(agentExecutionTimeoutSeconds) * time.Second,
		LLMOpAllowLiveCompletion:  viper.GetBool("LLMOP_ALLOW_LIVE_COMPLETION"),
		RequirementAnalysisMode:   requirementAnalysisMode,
		LegacyAPIEnabled:          viper.GetBool("LEGACY_API_ENABLED"),
	}
}

func ValidateServerConfig(config ServerConfig) error {
	if config.AppDeployPoCEnabled {
		switch {
		case config.LegacyAPIEnabled:
			return fmt.Errorf("choose either AIOPS_APPDEPLOY_POC_ENABLED or AIOPS_LEGACY_API_ENABLED")
		case strings.TrimSpace(config.AIAppBaseURL) != "":
			return fmt.Errorf("clear AIOPS_AIAPP_BASE_URL for AppDeploy PoC mode")
		case strings.TrimSpace(config.AppDeployBaseURL) == "":
			return fmt.Errorf("AIOPS_APPDEPLOY_BASE_URL is required in AppDeploy PoC mode")
		}
	}
	switch config.DeploymentAdapterMode {
	case "mock", "handoff":
	default:
		return fmt.Errorf("deployment adapter mode must be mock or handoff")
	}
	switch config.RequirementAnalysisMode {
	case requirementAnalysisModeLocalRule, requirementAnalysisModeQwen:
		return nil
	default:
		return fmt.Errorf("requirement analysis mode must be local_rule or qwen")
	}
}

func findRepoRoot() string {
	current, err := os.Getwd()
	if err != nil {
		return "."
	}

	for i := 0; i < 10; i++ {
		marker := filepath.Join(current, "config", "agent_registry.json")
		if _, err := os.Stat(marker); err == nil {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}
	return "."
}

func loadJSON[T any](path string) (T, error) {
	var value T
	bytes, err := os.ReadFile(path)
	if err != nil {
		return value, fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(bytes, &value); err != nil {
		return value, fmt.Errorf("parse %s: %w", path, err)
	}
	return value, nil
}

func (config ServerConfig) path(parts ...string) string {
	items := append([]string{config.RepoRoot}, parts...)
	return filepath.Join(items...)
}
