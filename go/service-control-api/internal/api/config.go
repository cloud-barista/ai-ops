package api

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/viper"
)

type ServerConfig struct {
	RepoRoot               string
	OpenAPIPath            string
	LLMCandidatesPath      string
	PlannerGuardPolicyPath string
	AppDeployBaseURL       string
	BindAddress            string
	AutonomyAdminToken     string
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
	llmCandidatesPath := viper.GetString("LLM_CANDIDATES_PATH")
	if llmCandidatesPath == "" {
		llmCandidatesPath = filepath.Join(repoRoot, "config", "ops_llm_eval_candidates.json")
	} else if !filepath.IsAbs(llmCandidatesPath) {
		llmCandidatesPath = filepath.Join(repoRoot, llmCandidatesPath)
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
	return ServerConfig{
		RepoRoot:               repoRoot,
		OpenAPIPath:            openAPIPath,
		LLMCandidatesPath:      llmCandidatesPath,
		PlannerGuardPolicyPath: plannerGuardPolicyPath,
		AppDeployBaseURL:       viper.GetString("APPDEPLOY_BASE_URL"),
		BindAddress:            bindAddress,
		AutonomyAdminToken:     viper.GetString("AUTONOMY_ADMIN_TOKEN"),
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
